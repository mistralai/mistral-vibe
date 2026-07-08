from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
import errno
import functools
import json
import os
from pathlib import Path
import shlex
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any, ClassVar, Literal, get_args
import uuid

from pydantic import AliasChoices, BaseModel, Field, model_validator
from tree_sitter import Language, Node, Parser
import tree_sitter_bash as tsbash

from vibe.core.paths import VIBE_HOME
from vibe.core.scratchpad import is_scratchpad_path
from vibe.core.tools.arity import build_session_pattern
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.builtins.bash import BashToolConfig
from vibe.core.tools.builtins.managed_bash import backend as managed_bash_backend
from vibe.core.tools.builtins.managed_bash.backend import (
    ManagedBashBackend,
    ManagedBashBackendError,
)
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import is_path_within_workdir
from vibe.core.types import ToolResultEvent, ToolStreamEvent
from vibe.core.utils import is_windows
from vibe.core.utils.io import decode_safe

if TYPE_CHECKING:
    from vibe.core.config import AnyVibeConfig

Status = Literal["running", "completed", "killed", "timed_out", "orphaned"]
LogAction = Literal["read", "write", "append"]
SessionAction = Literal["list", "inspect", "kill", "reset"]

DEFAULT_INLINE_BYTES = 30_000
DEFAULT_MAX_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_POLL_SECONDS = 300.0
KILL_GRACE_SECONDS = 2.0
READER_SELECT_SECONDS = 0.1

CONTROL_SEQUENCES: dict[str, bytes] = {
    "ctrl_@": b"\x00",
    "ctrl_a": b"\x01",
    "ctrl_b": b"\x02",
    "ctrl_c": b"\x03",
    "ctrl_d": b"\x04",
    "ctrl_e": b"\x05",
    "ctrl_f": b"\x06",
    "ctrl_g": b"\x07",
    "ctrl_h": b"\x08",
    "ctrl_i": b"\x09",
    "tab": b"\x09",
    "ctrl_j": b"\x0a",
    "enter": b"\r",
    "return": b"\r",
    "ctrl_k": b"\x0b",
    "ctrl_l": b"\x0c",
    "ctrl_m": b"\r",
    "ctrl_n": b"\x0e",
    "ctrl_o": b"\x0f",
    "ctrl_p": b"\x10",
    "ctrl_q": b"\x11",
    "ctrl_r": b"\x12",
    "ctrl_s": b"\x13",
    "ctrl_t": b"\x14",
    "ctrl_u": b"\x15",
    "ctrl_v": b"\x16",
    "ctrl_w": b"\x17",
    "ctrl_x": b"\x18",
    "ctrl_y": b"\x19",
    "ctrl_z": b"\x1a",
    "esc": b"\x1b",
    "escape": b"\x1b",
    "backspace": b"\x7f",
    "delete": b"\x1b[3~",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
}

ControlKey = Literal[
    "ctrl_@",
    "ctrl_a",
    "ctrl_b",
    "ctrl_c",
    "ctrl_d",
    "ctrl_e",
    "ctrl_f",
    "ctrl_g",
    "ctrl_h",
    "ctrl_i",
    "tab",
    "ctrl_j",
    "enter",
    "return",
    "ctrl_k",
    "ctrl_l",
    "ctrl_m",
    "ctrl_n",
    "ctrl_o",
    "ctrl_p",
    "ctrl_q",
    "ctrl_r",
    "ctrl_s",
    "ctrl_t",
    "ctrl_u",
    "ctrl_v",
    "ctrl_w",
    "ctrl_x",
    "ctrl_y",
    "ctrl_z",
    "esc",
    "escape",
    "backspace",
    "delete",
    "up",
    "down",
    "right",
    "left",
    "home",
    "end",
]

if set(get_args(ControlKey)) != set(CONTROL_SEQUENCES):
    raise RuntimeError("ControlKey is out of sync with CONTROL_SEQUENCES")


class ManagedBashError(Exception):
    pass


class SessionNotFoundError(ManagedBashError):
    pass


@functools.lru_cache(maxsize=1)
def _get_parser() -> Parser:
    return Parser(Language(tsbash.language()))


def _extract_commands(command: str) -> list[str]:
    parser = _get_parser()
    tree = parser.parse(command.encode("utf-8"))

    commands: list[str] = []

    def find_commands(node: Node) -> None:
        if node.type == "command":
            parts = []
            for child in node.children:
                if (
                    child.type
                    in {"command_name", "word", "string", "raw_string", "concatenation"}
                    and child.text is not None
                ):
                    parts.append(child.text.decode("utf-8"))
            # When a command has a heredoc (or other redirect), tree-sitter
            # wraps it in a redirected_statement and the redirect is a sibling
            # of the command node, not a child.  Without this check,
            # `python3 << 'EOF'` is extracted as bare `python3` and
            # incorrectly blocked by the standalone denylist.
            if parts and node.parent and node.parent.type == "redirected_statement":
                parts.append("<redirect>")
            if parts:
                commands.append(" ".join(parts))

        for child in node.children:
            find_commands(child)

    find_commands(tree.root_node)
    return commands


def _get_shell_executable() -> str | None:
    if is_windows():
        return None
    return os.environ.get("SHELL")


_READ_ONLY_COMMANDS_WINDOWS = ["dir", "findstr", "more", "type", "ver", "where"]
_READ_ONLY_COMMANDS_POSIX = [
    "basename",
    "cat",
    "comm",
    "cut",
    "date",
    "diff",
    "dirname",
    "du",
    "file",
    "find",
    "fmt",
    "fold",
    "grep",
    "head",
    "join",
    "less",
    "ls",
    "md5sum",
    "more",
    "nl",
    "od",
    "paste",
    "pwd",
    "readlink",
    "sha1sum",
    "sha256sum",
    "shasum",
    "sort",
    "stat",
    "sum",
    "tac",
    "tail",
    "tr",
    "uname",
    "uniq",
    "wc",
    "which",
]


def default_read_only_commands() -> list[str]:
    return list(
        _READ_ONLY_COMMANDS_WINDOWS if is_windows() else _READ_ONLY_COMMANDS_POSIX
    )


def _get_default_allowlist() -> list[str]:
    common = ["cd", "echo", "git diff", "git log", "git status", "tree", "whoami"]
    return common + default_read_only_commands()


def _get_default_denylist() -> list[str]:
    common = ["gdb", "pdb", "passwd"]

    if is_windows():
        return common + ["cmd /k", "powershell -NoExit", "pwsh -NoExit", "notepad"]

    return common + [
        "nano",
        "vim",
        "vi",
        "emacs",
        "bash -i",
        "sh -i",
        "zsh -i",
        "fish -i",
        "dash -i",
        "screen",
        "tmux",
    ]


def _get_default_denylist_standalone() -> list[str]:
    common = ["python", "python3", "ipython"]

    if is_windows():
        return common + ["cmd", "powershell", "pwsh", "notepad"]

    return common + ["bash", "sh", "nohup", "vi", "vim", "emacs", "nano", "su"]


_PATH_COMMANDS = {
    "cat",
    "cd",
    "chmod",
    "chown",
    "cp",
    "head",
    "ls",
    "mkdir",
    "mv",
    "rm",
    "stat",
    "tail",
    "touch",
    "wc",
}

_FIND_EXECUTION_PREDICATES = {"-exec", "-execdir", "-ok", "-okdir"}


def _split_command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _looks_like_path(token: str) -> bool:
    return (
        token.startswith(os.sep)
        or token.startswith("~")
        or token.startswith(".")
        or os.sep in token
    )


def _collect_outside_dirs(
    command_parts: list[str], command_cwd: Path | None = None
) -> set[str]:
    command_cwd = Path.cwd() if command_cwd is None else command_cwd
    dirs: set[str] = set()
    if not is_path_within_workdir(str(command_cwd)) and not is_scratchpad_path(
        str(command_cwd)
    ):
        dirs.add(str(command_cwd))

    for part in command_parts:
        tokens = _split_command_tokens(part)
        command = tokens[0] if tokens else None
        if not command or command not in _PATH_COMMANDS:
            continue
        for token in tokens[1:]:
            if token.startswith("-"):
                continue
            if command == "chmod" and token.startswith("+"):
                continue
            if not _looks_like_path(token):
                continue

            resolved = Path(token).expanduser()
            if not resolved.is_absolute():
                resolved = command_cwd / resolved
            resolved = resolved.resolve()

            if is_path_within_workdir(str(resolved)):
                continue
            if is_scratchpad_path(str(resolved)):
                continue

            parent = str(resolved) if resolved.is_dir() else str(resolved.parent)
            dirs.add(parent)
    return dirs


def _matches_pattern(command: str, pattern: str) -> bool:
    return command == pattern or command.startswith(pattern + " ")


def _matches_command_or_basename(command: str, pattern: str) -> bool:
    if _matches_pattern(command, pattern):
        return True
    parts = command.split()
    if not parts:
        return False
    base_command = os.path.basename(parts[0])
    normalized = " ".join([base_command, *parts[1:]])
    return _matches_pattern(normalized, pattern)


def _normalize_control_name(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def _decode_base64_bytes(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error as exc:
        raise ManagedBashError(f"invalid bytes_base64 value: {exc}") from exc


@dataclass
class TerminalSession:
    session_id: str
    command: str
    cwd: Path
    shell: str
    process: subprocess.Popen[bytes]
    master_fd: int
    output_path: Path
    manifest_path: Path
    created_at: float
    status: Status = "running"
    exit_code: int | None = None
    updated_at: float = field(default_factory=time.time)
    reader_error: str | None = None
    condition: threading.Condition = field(
        default_factory=lambda: threading.Condition(threading.RLock())
    )
    reader_thread: threading.Thread | None = None


class SessionInfo(BaseModel):
    session_id: str
    command: str
    cwd: str
    shell: str
    status: Status
    exit_code: int | None = None
    output_path: str
    created_at: str
    updated_at: str
    reader_error: str | None = None


class OutputChunk(BaseModel):
    output: str
    next_cursor: int
    truncated: bool


def _now_iso(timestamp: float | None = None) -> str:
    value = time.time() if timestamp is None else timestamp
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


def _decode_output(raw: bytes) -> str:
    return decode_safe(raw, from_subprocess=True).text


_UTF8_CONTINUATION_MIN = 0x80
_UTF8_LEAD_2 = 0xC0
_UTF8_LEAD_3 = 0xE0
_UTF8_LEAD_4 = 0xF0
_UTF8_LEAD_MAX = 0xF8
_UTF8_MAX_SEQUENCE = 4


def _utf8_sequence_length(lead: int) -> int | None:
    if lead < _UTF8_CONTINUATION_MIN:
        return 1
    if _UTF8_LEAD_2 <= lead < _UTF8_LEAD_3:
        return 2
    if _UTF8_LEAD_3 <= lead < _UTF8_LEAD_4:
        return 3
    if _UTF8_LEAD_4 <= lead < _UTF8_LEAD_MAX:
        return 4
    return None


def _trim_incomplete_utf8_suffix(raw: bytes) -> bytes:
    # When paging output by byte offset, the window may end in the middle of a
    # multi-byte UTF-8 character. Drop the dangling lead+continuation bytes so
    # the caller re-reads them on the next poll instead of decoding a U+FFFD.
    for back in range(1, min(_UTF8_MAX_SEQUENCE, len(raw)) + 1):
        byte = raw[-back]
        if _UTF8_CONTINUATION_MIN <= byte < _UTF8_LEAD_2:
            continue
        expected = _utf8_sequence_length(byte)
        if expected is not None and back < expected:
            return raw[:-back]
        return raw
    return raw


def _skip_utf8_continuation_prefix(path: Path, cursor: int) -> int:
    if cursor <= 0:
        return cursor
    with path.open("rb") as handle:
        handle.seek(cursor)
        prefix = handle.read(_UTF8_MAX_SEQUENCE - 1)
    for index, byte in enumerate(prefix):
        if _UTF8_CONTINUATION_MIN <= byte < _UTF8_LEAD_2:
            continue
        return cursor + index
    return cursor + len(prefix)


def _safe_stat_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


class TerminalSessionManager:
    def __init__(self, backend: ManagedBashBackend | None = None) -> None:
        self._backend = backend or managed_bash_backend.create_managed_bash_backend()
        self.base_dir = VIBE_HOME.path / "bash-tool"
        self.sessions_dir = self.base_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, TerminalSession] = {}
        self._orphaned: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def start(
        self,
        *,
        command: str,
        cwd: Path,
        env: dict[str, str] | None,
        shell: str,
        background: bool,
    ) -> TerminalSession:
        if not command.strip():
            raise ManagedBashError("command must not be empty")
        if not cwd.is_dir():
            raise ManagedBashError(f"cwd is not a directory: {cwd}")

        merged_env = self._build_env(env)

        session_id = self._new_session_id()
        output_path = self.sessions_dir / f"{session_id}.log"
        manifest_path = self.sessions_dir / f"{session_id}.json"
        output_path.touch()

        master_fd, slave_fd = self._backend.open_terminal()
        try:
            process = self._backend.spawn(
                shell=shell, command=command, cwd=cwd, env=merged_env, slave_fd=slave_fd
            )
        except Exception:
            self._backend.close_fd(master_fd)
            self._backend.close_fd(slave_fd)
            raise
        finally:
            try:
                self._backend.close_fd(slave_fd)
            except OSError:
                pass

        session = TerminalSession(
            session_id=session_id,
            command=command,
            cwd=cwd,
            shell=shell,
            process=process,
            master_fd=master_fd,
            output_path=output_path,
            manifest_path=manifest_path,
            created_at=time.time(),
        )
        reader = threading.Thread(
            target=self._reader_loop,
            args=(session,),
            name=f"managed-bash-reader-{session_id}",
            daemon=True,
        )
        session.reader_thread = reader

        with self._lock:
            self._sessions[session_id] = session
            self._orphaned.pop(session_id, None)
            self._save_manifest(session)

        reader.start()
        return session

    def resolve_shell(self, requested: str | None, configured: str | None) -> str:
        return self._backend.resolve_shell(requested, configured)

    def wait_for_exit(self, session_id: str, timeout_seconds: float) -> bool:
        session = self._live_session(session_id)
        deadline = time.monotonic() + timeout_seconds
        with session.condition:
            while session.status == "running":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                session.condition.wait(timeout=remaining)
            return True

    def write_stdin(self, session_id: str, text: str) -> int:
        return self.write_bytes(session_id, text.encode("utf-8"))

    def write_bytes(self, session_id: str, data: bytes) -> int:
        if not data:
            raise ManagedBashError("stdin payload must not be empty")
        session = self._live_session(session_id)
        with session.condition:
            if session.status != "running":
                raise ManagedBashError(
                    f"cannot write to session {session_id}; status is {session.status}"
                )
            try:
                return self._backend.write(session.master_fd, data)
            except OSError as exc:
                raise ManagedBashError(f"failed to write stdin: {exc}") from exc

    def read_output(
        self, *, session_id: str, cursor: int, wait_seconds: float, max_bytes: int
    ) -> tuple[SessionInfo, OutputChunk]:
        session = self._sessions.get(session_id)
        if session is None:
            info = self._orphan_info(session_id)
            chunk = self._read_file_chunk(
                Path(info.output_path), cursor=cursor, max_bytes=max_bytes
            )
            return info, chunk

        deadline = time.monotonic() + wait_seconds
        with session.condition:
            while True:
                self._refresh_session_locked(session)
                available = _safe_stat_size(session.output_path) - cursor
                expired = time.monotonic() >= deadline
                if session.status != "running" or available >= max_bytes or expired:
                    break
                remaining = max(0.0, deadline - time.monotonic())
                session.condition.wait(timeout=min(READER_SELECT_SECONDS, remaining))

            info = self._session_info_locked(session)
            chunk = self._read_file_chunk(
                session.output_path,
                cursor=cursor,
                max_bytes=max_bytes,
                trim_final_incomplete_utf8=info.status == "running",
            )
            return info, chunk

    def inspect_session(
        self, session_id: str, max_bytes: int
    ) -> tuple[SessionInfo, OutputChunk]:
        info = self.info(session_id)
        output_path = Path(info.output_path)
        size = _safe_stat_size(output_path)
        cursor = _skip_utf8_continuation_prefix(output_path, max(0, size - max_bytes))
        chunk = self._read_file_chunk(
            output_path,
            cursor=cursor,
            max_bytes=max_bytes,
            trim_final_incomplete_utf8=info.status == "running",
        )
        return info, chunk

    def info(self, session_id: str) -> SessionInfo:
        session = self._sessions.get(session_id)
        if session is None:
            return self._orphan_info(session_id)
        with session.condition:
            self._refresh_session_locked(session)
            return self._session_info_locked(session)

    def list_sessions(self) -> list[SessionInfo]:
        result: list[SessionInfo] = []
        with self._lock:
            for session in self._sessions.values():
                with session.condition:
                    self._refresh_session_locked(session)
                    result.append(self._session_info_locked(session))
            for session_id in sorted(self._orphaned):
                if session_id not in self._sessions:
                    result.append(self._info_from_manifest(self._orphaned[session_id]))
        return sorted(result, key=lambda item: item.created_at)

    def kill(self, session_id: str, *, status: Status = "killed") -> SessionInfo:
        if status not in {"killed", "timed_out"}:
            raise ManagedBashError(f"invalid terminal kill status: {status}")

        session = self._live_session(session_id)
        with session.condition:
            if session.status != "running":
                return self._session_info_locked(session)
            session.status = status
            session.updated_at = time.time()
            session.condition.notify_all()

        self._terminate_process_group(session)
        if session.reader_thread is not None:
            session.reader_thread.join(timeout=KILL_GRACE_SECONDS)

        with session.condition:
            self._refresh_session_locked(session)
            self._save_manifest(session)
            return self._session_info_locked(session)

    def reset(self, *, clear_logs: bool) -> list[SessionInfo]:
        with self._lock:
            killed: list[SessionInfo] = []
            for session in list(self._sessions.values()):
                with session.condition:
                    self._refresh_session_locked(session)
                    running = session.status == "running"
                if running:
                    killed.append(self.kill(session.session_id))

            if clear_logs:
                self._sessions.clear()
                self._orphaned.clear()
                for child in self.sessions_dir.glob("*"):
                    if child.is_file():
                        child.unlink(missing_ok=True)
        return killed

    def resolve_log_path(
        self, *, session_id: str | None, relative_path: str | None
    ) -> Path:
        if session_id:
            return Path(self.info(session_id).output_path)
        if not relative_path:
            raise ManagedBashError("provide either session_id or relative_path")
        candidate = (self.base_dir / relative_path).resolve()
        base = self.base_dir.resolve()
        if not candidate.is_relative_to(base):
            raise ManagedBashError("log path must stay under ~/.vibe/bash-tool")
        return candidate

    def read_log_file(self, path: Path, *, offset: int, max_bytes: int) -> OutputChunk:
        return self._read_file_chunk(
            path,
            cursor=offset,
            max_bytes=max_bytes,
            trim_final_incomplete_utf8=self._is_running_output_path(path),
        )

    def write_log_file(self, path: Path, *, action: LogAction, content: str) -> int:
        resolved = path.resolve()
        base = self.base_dir.resolve()
        if not resolved.is_relative_to(base):
            raise ManagedBashError("log path must stay under ~/.vibe/bash-tool")
        self._reject_live_session_log_write(resolved)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if action == "write":
            resolved.write_text(content, encoding="utf-8")
        elif action == "append":
            with resolved.open("a", encoding="utf-8") as handle:
                handle.write(content)
        else:
            raise ManagedBashError(f"unsupported write action: {action}")
        return len(content.encode("utf-8"))

    def _reject_live_session_log_write(self, path: Path) -> None:
        with self._lock:
            for session in self._sessions.values():
                with session.condition:
                    if session.output_path.resolve() != path:
                        continue
                    self._refresh_session_locked(session)
                    if session.status == "running":
                        raise ManagedBashError(
                            "cannot write or append to a live session log; use "
                            "BashStdin for process input or wait until the session exits"
                        )
                    return

    def _is_running_output_path(self, path: Path) -> bool:
        resolved = path.resolve()
        with self._lock:
            for session in self._sessions.values():
                with session.condition:
                    if session.output_path.resolve() != resolved:
                        continue
                    self._refresh_session_locked(session)
                    return session.status == "running"
        return False

    def _reader_loop(self, session: TerminalSession) -> None:
        try:
            while True:
                readable = self._backend.wait_readable(
                    session.master_fd, READER_SELECT_SECONDS
                )
                if readable:
                    try:
                        chunk = self._backend.read(session.master_fd, 8192)
                    except OSError as exc:
                        if exc.errno in {errno.EBADF, errno.EIO}:
                            break
                        raise
                    if not chunk:
                        break
                    self._append_output(session, chunk)

                if session.process.poll() is not None and not readable:
                    break
        except Exception as exc:
            with session.condition:
                session.reader_error = str(exc)
                session.updated_at = time.time()
                session.condition.notify_all()
        finally:
            try:
                session.process.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self._terminate_process_group(session, force=True)
            except Exception:
                pass

            with session.condition:
                if session.status == "running":
                    session.status = "completed"
                session.exit_code = session.process.returncode
                session.updated_at = time.time()
                session.condition.notify_all()
                self._save_manifest(session)

            try:
                self._backend.close_fd(session.master_fd)
            except OSError:
                pass

    def _append_output(self, session: TerminalSession, data: bytes) -> None:
        with session.condition:
            with session.output_path.open("ab") as handle:
                handle.write(data)
            session.updated_at = time.time()
            session.condition.notify_all()

    def _terminate_process_group(
        self, session: TerminalSession, *, force: bool = False
    ) -> None:
        self._backend.terminate_process_group(
            session.process, force=force, grace_seconds=KILL_GRACE_SECONDS
        )

    def _refresh_session_locked(self, session: TerminalSession) -> None:
        if session.status != "running":
            return
        returncode = session.process.poll()
        if returncode is None:
            return
        session.status = "completed"
        session.exit_code = returncode
        session.updated_at = time.time()
        session.condition.notify_all()
        self._save_manifest(session)

    def _build_env(self, overrides: dict[str, str] | None) -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "TERM": env.get("TERM", "xterm-256color"),
            "COLUMNS": env.get("COLUMNS", "120"),
            "LINES": env.get("LINES", "40"),
            # Keep the PTY interactive (so stdin can drive REPLs/prompts) while
            # neutralising pagers: with a real TTY, foreground commands like
            # `git log` would otherwise spawn `less` and block until timeout
            # waiting for input that never arrives.
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "LESS": "-FX",
            "DEBIAN_FRONTEND": "noninteractive",
        })
        if overrides:
            env.update(overrides)
        return env

    def _live_session(self, session_id: str) -> TerminalSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            if session_id in self._orphaned:
                raise ManagedBashError(f"session is orphaned: {session_id}") from exc
            raise SessionNotFoundError(f"unknown bash session: {session_id}") from exc

    def _orphan_info(self, session_id: str) -> SessionInfo:
        try:
            return self._info_from_manifest(self._orphaned[session_id])
        except KeyError as exc:
            raise SessionNotFoundError(f"unknown bash session: {session_id}") from exc

    def _session_info_locked(self, session: TerminalSession) -> SessionInfo:
        return SessionInfo(
            session_id=session.session_id,
            command=session.command,
            cwd=str(session.cwd),
            shell=session.shell,
            status=session.status,
            exit_code=session.exit_code,
            output_path=str(session.output_path),
            created_at=_now_iso(session.created_at),
            updated_at=_now_iso(session.updated_at),
            reader_error=session.reader_error,
        )

    def _session_metadata(self, session: TerminalSession) -> dict[str, Any]:
        info = self._session_info_locked(session)
        return info.model_dump()

    def _save_manifest(self, session: TerminalSession) -> None:
        metadata = self._session_metadata(session)
        session.manifest_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _load_orphaned_manifests(self) -> None:
        # Orphans are sessions persisted on disk by an earlier Vibe process: the
        # live PTY/process is gone but the manifest and log remain. They are
        # read-only here (inspectable, not killable/writable); one that was still
        # "running" at exit is recorded as "orphaned" since the process is lost.
        for manifest_path in self.sessions_dir.glob("*.json"):
            try:
                metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            session_id = metadata.get("session_id")
            if not isinstance(session_id, str):
                continue
            if metadata.get("status") == "running":
                metadata["status"] = "orphaned"
                metadata["updated_at"] = _now_iso()
                try:
                    manifest_path.write_text(
                        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
                    )
                except OSError:
                    pass
            self._orphaned[session_id] = metadata

    def _info_from_manifest(self, metadata: dict[str, Any]) -> SessionInfo:
        return SessionInfo.model_validate(metadata)

    def _read_file_chunk(
        self,
        path: Path,
        *,
        cursor: int,
        max_bytes: int,
        trim_final_incomplete_utf8: bool = False,
    ) -> OutputChunk:
        if cursor < 0:
            raise ManagedBashError("cursor must be a non-negative byte offset")
        if max_bytes <= 0:
            raise ManagedBashError("max_bytes must be a positive byte limit")
        if not path.exists():
            return OutputChunk(output="", next_cursor=cursor, truncated=False)

        size = _safe_stat_size(path)
        safe_cursor = min(cursor, size)
        with path.open("rb") as handle:
            handle.seek(safe_cursor)
            raw = handle.read(max_bytes)
        if trim_final_incomplete_utf8 or size > safe_cursor + len(raw):
            raw = _trim_incomplete_utf8_suffix(raw)
        next_cursor = safe_cursor + len(raw)
        return OutputChunk(
            output=_decode_output(raw),
            next_cursor=next_cursor,
            truncated=size > next_cursor,
        )

    def _new_session_id(self) -> str:
        stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        return f"bash_{stamp}_{uuid.uuid4().hex[:8]}"


_MANAGER: TerminalSessionManager | None = None


def _manager() -> TerminalSessionManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = TerminalSessionManager()
    return _MANAGER


def _experimental_bash_enabled(config: AnyVibeConfig | None) -> bool:
    return bool(
        config
        and config.experimental_bash_tool
        and managed_bash_backend.managed_bash_supported()
    )


class ExperimentalBashToolConfig(BashToolConfig):
    max_timeout_seconds: float = Field(
        default=DEFAULT_MAX_TIMEOUT_SECONDS,
        description="Maximum foreground wait time allowed for one tool call.",
    )
    max_inline_bytes: int = Field(
        default=DEFAULT_INLINE_BYTES,
        validation_alias=AliasChoices("max_inline_bytes", "max_inline_chars"),
        description="Maximum output bytes read before inline decoding.",
    )
    shell: str | None = Field(
        default=None, description="Optional default shell executable override."
    )


class BashOutputConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    max_poll_seconds: float = Field(
        default=DEFAULT_MAX_POLL_SECONDS,
        description="Maximum long-poll wait window for output polling.",
    )
    max_inline_bytes: int = Field(
        default=DEFAULT_INLINE_BYTES,
        validation_alias=AliasChoices("max_inline_bytes", "max_inline_chars"),
        description="Maximum output bytes read before inline decoding.",
    )


class BashStdinConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class BashSessionsConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    max_inline_bytes: int = Field(
        default=DEFAULT_INLINE_BYTES,
        validation_alias=AliasChoices("max_inline_bytes", "max_inline_chars"),
        description="Maximum output bytes read before inline decoding.",
    )


class BashLogFileConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    max_inline_bytes: int = Field(
        default=DEFAULT_INLINE_BYTES,
        validation_alias=AliasChoices("max_inline_bytes", "max_inline_chars"),
        description="Maximum file bytes read before inline decoding.",
    )


class ExperimentalBashArgs(BaseModel):
    command: str = Field(description="Shell command to run.")
    timeout: int | None = Field(
        default=None,
        description="Backward-compatible hard timeout override in seconds.",
    )
    background: bool = Field(
        default=False, description="Return immediately with a live session."
    )
    timeout_seconds: float | None = Field(
        default=None,
        ge=0,
        description="Foreground wait time before soft or hard timeout handling.",
    )
    hard_timeout: bool = Field(
        default=False,
        description="Kill the process group when timeout_seconds expires.",
    )
    cwd: str | None = Field(default=None, description="Working directory override.")
    env: dict[str, str] | None = Field(
        default=None, description="Environment variable overrides."
    )
    shell: str | None = Field(default=None, description="Shell executable override.")


class ExperimentalBashResult(BaseModel):
    command: str
    session_id: str = ""
    status: Status = "completed"
    exit_code: int | None = None
    background: bool = False
    output: str = ""
    next_cursor: int = 0
    truncated: bool = False
    output_path: str = ""
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class BashOutputArgs(BaseModel):
    session_id: str
    cursor: int | None = Field(
        default=None, ge=0, description="Byte offset returned by next_cursor."
    )
    wait_seconds: float = Field(default=0, ge=0)
    max_bytes: int | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices("max_bytes", "max_chars"),
        description="Maximum output bytes read before inline decoding.",
    )


class BashOutputResult(BaseModel):
    session_id: str
    status: Status
    exit_code: int | None = None
    output: str
    next_cursor: int
    truncated: bool
    output_path: str


class BashStdinArgs(BaseModel):
    session_id: str
    text: str | None = Field(
        default=None,
        description="UTF-8 text to send exactly as provided. Include \\n for Enter.",
    )
    control: list[ControlKey] = Field(
        default_factory=list,
        description=(
            "Named control sequences to send, for example ctrl_c, ctrl_d, ctrl_z, "
            "esc, tab, enter, backspace, up, down, left, right."
        ),
    )
    bytes_base64: str | None = Field(
        default=None, description="Raw bytes to send, encoded as base64."
    )

    @model_validator(mode="after")
    def _exactly_one_input(self) -> BashStdinArgs:
        sources = (
            self.text is not None,
            bool(self.control),
            self.bytes_base64 is not None,
        )
        if sum(sources) != 1:
            raise ValueError("provide exactly one of text, control, or bytes_base64")
        return self


class BashStdinResult(BaseModel):
    session_id: str
    bytes_written: int
    status: Status


class BashSessionsArgs(BaseModel):
    action: SessionAction = Field(default="list")
    session_id: str | None = None
    clear_logs: bool = Field(
        default=False, description="When resetting, also delete stored logs."
    )
    max_bytes: int | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices("max_bytes", "max_chars"),
        description="Maximum output bytes read before inline decoding.",
    )


class BashSessionsResult(BaseModel):
    action: SessionAction
    sessions: list[SessionInfo] = Field(default_factory=list)
    session: SessionInfo | None = None
    output: str | None = None
    next_cursor: int | None = None
    truncated: bool | None = None
    message: str | None = None


class BashLogFileArgs(BaseModel):
    action: LogAction
    session_id: str | None = None
    relative_path: str | None = None
    offset: int = Field(default=0, ge=0, description="Byte offset to start reading.")
    max_bytes: int | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices("max_bytes", "max_chars"),
        description="Maximum file bytes read before inline decoding.",
    )
    content: str | None = None


class BashLogFileResult(BaseModel):
    action: LogAction
    path: str
    content: str | None = None
    next_cursor: int | None = None
    truncated: bool | None = None
    bytes_written: int | None = None


class _BashPermissionMixin[ConfigT: BashToolConfig]:
    if TYPE_CHECKING:

        @property
        def config(self) -> ConfigT: ...

    @staticmethod
    def _has_find_execution_predicate(command: str) -> bool:
        if not _matches_pattern(command, "find"):
            return False
        return any(predicate in command for predicate in _FIND_EXECUTION_PREDICATES)

    @staticmethod
    def _build_command_required_permission(
        invocation_pattern: str, session_pattern: str, label: str
    ) -> RequiredPermission:
        return RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern=invocation_pattern,
            session_pattern=session_pattern,
            label=label,
        )

    @staticmethod
    def _build_outside_directory_permission(glob: str) -> RequiredPermission:
        return RequiredPermission(
            scope=PermissionScope.OUTSIDE_DIRECTORY,
            invocation_pattern=glob,
            session_pattern=glob,
            label=f"outside workdir ({glob})",
        )

    def _find_denylist_match(self, command: str) -> str | None:
        return next(
            (
                pattern
                for pattern in self.config.denylist
                if _matches_command_or_basename(command, pattern)
            ),
            None,
        )

    def _is_standalone_denylisted(self, command: str) -> bool:
        parts = command.split()
        if not parts:
            return False
        base_command = parts[0]
        if len(parts) != 1:
            return False
        command_name = os.path.basename(base_command)
        return (
            command_name in self.config.denylist_standalone
            or base_command in self.config.denylist_standalone
        )

    def _is_allowlisted(self, command: str) -> bool:
        return any(
            _matches_pattern(command, pattern) for pattern in self.config.allowlist
        )

    def _is_sensitive(self, command: str) -> bool:
        tokens = command.split()
        if not tokens:
            return False
        return tokens[0] in self.config.sensitive_patterns

    def _resolve_guardrail_permission(
        self, command_parts: list[str]
    ) -> PermissionContext | None:
        find_execution_required: list[RequiredPermission] = []
        seen_find_execution: set[str] = set()

        for part in command_parts:
            if matched := self._find_denylist_match(part):
                return PermissionContext(
                    permission=ToolPermission.NEVER,
                    reason=f"Command denied: '{part}' matches denylist pattern '{matched}'. Do not attempt to run this command.",
                )
            if self._is_standalone_denylisted(part):
                return PermissionContext(
                    permission=ToolPermission.NEVER,
                    reason=f"Command denied: '{part}' is not allowed as a standalone command. Do not attempt to run this command.",
                )
            if not self._has_find_execution_predicate(part):
                continue
            if part in seen_find_execution:
                continue
            seen_find_execution.add(part)
            find_execution_required.append(
                self._build_command_required_permission(
                    invocation_pattern=part, session_pattern=part, label=part
                )
            )

        if not find_execution_required:
            return None
        return PermissionContext(
            permission=ToolPermission.ASK, required_permissions=find_execution_required
        )

    def _is_unconditionally_allowed(
        self,
        command_parts: list[str],
        outside_dirs: set[str],
        required_context_permissions: list[RequiredPermission] | None = None,
    ) -> bool:
        required_context_permissions = required_context_permissions or []
        if any(self._is_sensitive(part) for part in command_parts):
            return False
        if required_context_permissions:
            return False
        if self.config.permission == ToolPermission.ALWAYS:
            return True
        return all(self._is_allowlisted(part) for part in command_parts) and (
            not outside_dirs
        )

    def _build_required_permissions(
        self,
        command_parts: list[str],
        outside_dirs: set[str],
        required_context_permissions: list[RequiredPermission] | None = None,
    ) -> list[RequiredPermission]:
        required_context_permissions = required_context_permissions or []
        required: list[RequiredPermission] = []
        seen_session: set[str] = set()

        for part in command_parts:
            if not part:
                continue
            tokens = part.split()
            if not tokens:
                continue

            is_sensitive = self._is_sensitive(part)
            if not is_sensitive and self._is_allowlisted(part):
                continue

            if is_sensitive:
                required.append(
                    self._build_command_required_permission(
                        invocation_pattern=part, session_pattern=part, label=part
                    )
                )
                continue

            session_pattern = build_session_pattern(tokens)
            if session_pattern in seen_session:
                continue
            seen_session.add(session_pattern)
            required.append(
                self._build_command_required_permission(
                    invocation_pattern=part,
                    session_pattern=session_pattern,
                    label=session_pattern,
                )
            )

        for glob in sorted(str(Path(directory) / "*") for directory in outside_dirs):
            required.append(self._build_outside_directory_permission(glob))

        required.extend(required_context_permissions)
        return required


class _DescriptionOnlyPromptMixin:
    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return None


class ExperimentalBash(
    _BashPermissionMixin[ExperimentalBashToolConfig],
    BaseTool[
        ExperimentalBashArgs,
        ExperimentalBashResult,
        ExperimentalBashToolConfig,
        BaseToolState,
    ],
    ToolUIData[ExperimentalBashArgs, ExperimentalBashResult],
):
    description: ClassVar[str] = "Run a shell command in a managed PTY session."
    selection_priority: ClassVar[int] = 10

    @classmethod
    def get_name(cls) -> str:
        return "bash"

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _experimental_bash_enabled(config)

    @classmethod
    def format_call_display(cls, args: ExperimentalBashArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"bash: {args.command}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, ExperimentalBashResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        status = event.result.status
        message = f"Ran {event.result.command}"
        if status == "running":
            message = f"Running {event.result.command}"
        return ToolResultDisplay(success=event.error is None, message=message)

    @classmethod
    def get_status_text(cls) -> str:
        return "Running command"

    def _build_context_permissions(
        self, args: ExperimentalBashArgs
    ) -> list[RequiredPermission]:
        required: list[RequiredPermission] = []
        if args.shell:
            required.append(
                self._build_command_required_permission(
                    invocation_pattern=f"shell override: {args.shell}",
                    session_pattern=f"shell override: {args.shell}",
                    label=f"custom shell ({args.shell})",
                )
            )
        if args.env:
            names = ", ".join(sorted(args.env))
            required.append(
                self._build_command_required_permission(
                    invocation_pattern=f"env override: {names}",
                    session_pattern="env override *",
                    label=f"custom environment ({names})",
                )
            )
        return required

    def resolve_permission(
        self, args: ExperimentalBashArgs
    ) -> PermissionContext | None:
        if is_windows():
            return PermissionContext(
                permission=ToolPermission.NEVER,
                reason="managed bash requires a POSIX-like platform",
            )

        command_parts = _extract_commands(args.command)
        if not command_parts:
            return None

        guardrail_permission = self._resolve_guardrail_permission(command_parts)
        if (
            guardrail_permission
            and guardrail_permission.permission == ToolPermission.NEVER
        ):
            return guardrail_permission

        command_cwd = (
            Path(args.cwd).expanduser().resolve()
            if args.cwd is not None
            else Path.cwd()
        )
        outside_dirs = _collect_outside_dirs(command_parts, command_cwd)
        context_required = self._build_context_permissions(args)
        if (
            self._is_unconditionally_allowed(
                command_parts, outside_dirs, context_required
            )
            and not guardrail_permission
        ):
            return PermissionContext(permission=ToolPermission.ALWAYS)

        required = self._build_required_permissions(
            command_parts, outside_dirs, context_required
        )
        if guardrail_permission:
            required.extend(guardrail_permission.required_permissions)
        if not required:
            return None

        return PermissionContext(
            permission=ToolPermission.ASK, required_permissions=required
        )

    async def run(
        self, args: ExperimentalBashArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ExperimentalBashResult, None]:
        _ = ctx
        requested_timeout = (
            float(args.timeout) if args.timeout is not None else args.timeout_seconds
        )
        hard_timeout = args.hard_timeout or args.timeout is not None
        timeout = self._resolve_timeout(requested_timeout)
        max_bytes = self.config.max_output_bytes
        try:
            cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd()
            shell = _manager().resolve_shell(args.shell, self.config.shell)
            session = await asyncio.to_thread(
                _manager().start,
                command=args.command,
                cwd=cwd,
                env=args.env,
                shell=shell,
                background=args.background,
            )
            if args.background:
                yield self._result_from_session(session.session_id, True, max_bytes)
                return

            completed = await asyncio.to_thread(
                _manager().wait_for_exit, session.session_id, timeout
            )
            if completed:
                yield self._result_from_session(
                    session.session_id,
                    background=False,
                    max_bytes=max_bytes,
                    enforce_success=True,
                )
                return

            if not hard_timeout:
                yield self._result_from_session(session.session_id, True, max_bytes)
                return

            info = await asyncio.to_thread(
                _manager().kill, session.session_id, status="timed_out"
            )
            chunk = _manager().read_log_file(
                Path(info.output_path), offset=0, max_bytes=max_bytes
            )
            raise ToolError(
                "Command timed out after "
                f"{timeout:g}s: {args.command!r}\n"
                f"session_id: {info.session_id}\n"
                f"status: {info.status}\n"
                f"output_path: {info.output_path}\n"
                f"output:\n{chunk.output}"
            )
        except ToolError:
            raise
        except (ManagedBashError, ManagedBashBackendError) as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"Error running command {args.command!r}: {exc}") from exc

    def _resolve_timeout(self, requested: float | None) -> float:
        timeout = self.config.default_timeout if requested is None else requested
        return min(timeout, self.config.max_timeout_seconds)

    def _result_from_session(
        self,
        session_id: str,
        background: bool,
        max_bytes: int,
        *,
        enforce_success: bool = False,
    ) -> ExperimentalBashResult:
        info, chunk = _manager().read_output(
            session_id=session_id, cursor=0, wait_seconds=0, max_bytes=max_bytes
        )
        returncode = info.exit_code or 0
        if enforce_success and (info.status != "completed" or returncode != 0):
            error_msg = f"Command failed: {info.command!r}\n"
            error_msg += f"Return code: {returncode}"
            if info.status != "completed":
                error_msg += f"\nStatus: {info.status}"
            if chunk.output:
                error_msg += f"\nStdout: {chunk.output}"
            raise ToolError(error_msg.strip())

        normalized_output = chunk.output.replace("\r\n", "\n")
        return ExperimentalBashResult(
            command=info.command,
            session_id=info.session_id,
            status=info.status,
            exit_code=info.exit_code,
            background=background,
            output=chunk.output,
            next_cursor=chunk.next_cursor,
            truncated=chunk.truncated,
            output_path=info.output_path,
            stdout=normalized_output,
            stderr="",
            returncode=returncode,
        )


class BashOutput(
    _DescriptionOnlyPromptMixin,
    BaseTool[BashOutputArgs, BashOutputResult, BashOutputConfig, BaseToolState],
    ToolUIData[BashOutputArgs, BashOutputResult],
):
    description: ClassVar[str] = "Poll output from a running or completed bash session."

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _experimental_bash_enabled(config)

    @classmethod
    def format_call_display(cls, args: BashOutputArgs) -> ToolCallDisplay:
        if args.wait_seconds > 0:
            return ToolCallDisplay(
                summary=f"Waiting for bash session {args.session_id}"
            )
        return ToolCallDisplay(summary=f"Polling bash session {args.session_id}")

    @classmethod
    def format_result_display(cls, result: BashOutputResult) -> ToolResultDisplay:
        match result.status:
            case "running":
                message = f"Session {result.session_id} is still running"
            case "completed":
                message = f"Session {result.session_id} completed"
            case "killed":
                message = f"Session {result.session_id} was killed"
            case "timed_out":
                message = f"Session {result.session_id} timed out"
            case "orphaned":
                message = f"Session {result.session_id} is orphaned"
        suffix = "truncated" if result.truncated else ""
        return ToolResultDisplay(
            success=result.status in {"running", "completed", "orphaned"},
            message=message,
            suffix=suffix,
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Polling bash session"

    async def run(
        self, args: BashOutputArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BashOutputResult, None]:
        _ = ctx
        cursor = 0 if args.cursor is None else args.cursor
        wait_seconds = min(args.wait_seconds, self.config.max_poll_seconds)
        max_bytes = args.max_bytes or self.config.max_inline_bytes
        try:
            info, chunk = await asyncio.to_thread(
                _manager().read_output,
                session_id=args.session_id,
                cursor=cursor,
                wait_seconds=wait_seconds,
                max_bytes=max_bytes,
            )
        except (ManagedBashError, ManagedBashBackendError) as exc:
            raise ToolError(str(exc)) from exc
        yield BashOutputResult(
            session_id=info.session_id,
            status=info.status,
            exit_code=info.exit_code,
            output=chunk.output,
            next_cursor=chunk.next_cursor,
            truncated=chunk.truncated,
            output_path=info.output_path,
        )


class BashStdin(
    _DescriptionOnlyPromptMixin,
    BaseTool[BashStdinArgs, BashStdinResult, BashStdinConfig, BaseToolState],
    ToolUIData[BashStdinArgs, BashStdinResult],
):
    description: ClassVar[str] = "Send input to an interactive bash session."

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _experimental_bash_enabled(config)

    @classmethod
    def format_call_display(cls, args: BashStdinArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Sending input to bash session {args.session_id}"
        )

    @classmethod
    def format_result_display(cls, result: BashStdinResult) -> ToolResultDisplay:
        return ToolResultDisplay(
            success=result.status in {"running", "completed"},
            message=(
                f"Sent {result.bytes_written} bytes to "
                f"{result.status} session {result.session_id}"
            ),
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Sending bash input"

    async def run(
        self, args: BashStdinArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BashStdinResult, None]:
        _ = ctx
        try:
            payload = self._build_payload(args)
            bytes_written = await asyncio.to_thread(
                _manager().write_bytes, args.session_id, payload
            )
            info = _manager().info(args.session_id)
        except (ManagedBashError, ManagedBashBackendError) as exc:
            raise ToolError(str(exc)) from exc
        yield BashStdinResult(
            session_id=args.session_id, bytes_written=bytes_written, status=info.status
        )

    def _build_payload(self, args: BashStdinArgs) -> bytes:
        chunks: list[bytes] = []
        if args.text is not None:
            chunks.append(args.text.encode("utf-8"))

        for control_name in args.control:
            normalized = _normalize_control_name(control_name)
            sequence = CONTROL_SEQUENCES.get(normalized)
            if sequence is None:
                supported = ", ".join(sorted(CONTROL_SEQUENCES))
                raise ManagedBashError(
                    f"unsupported control sequence {control_name!r}; supported: {supported}"
                )
            chunks.append(sequence)

        if args.bytes_base64 is not None:
            chunks.append(_decode_base64_bytes(args.bytes_base64))
        return b"".join(chunks)


class BashSessions(
    _DescriptionOnlyPromptMixin,
    BaseTool[BashSessionsArgs, BashSessionsResult, BashSessionsConfig, BaseToolState],
    ToolUIData[BashSessionsArgs, BashSessionsResult],
):
    description: ClassVar[str] = "List, inspect, kill, or reset managed bash sessions."

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _experimental_bash_enabled(config)

    @classmethod
    def format_call_display(cls, args: BashSessionsArgs) -> ToolCallDisplay:
        match args.action:
            case "list":
                return ToolCallDisplay(summary="Listing bash sessions")
            case "inspect":
                return ToolCallDisplay(
                    summary=f"Inspecting bash session {args.session_id or ''}".strip()
                )
            case "kill":
                return ToolCallDisplay(
                    summary=f"Killing bash session {args.session_id or ''}".strip()
                )
            case "reset":
                return ToolCallDisplay(summary="Resetting bash sessions")

    @classmethod
    def format_result_display(cls, result: BashSessionsResult) -> ToolResultDisplay:
        match result.action:
            case "list":
                count = len(result.sessions)
                noun = "session" if count == 1 else "sessions"
                message = f"Found {count} bash {noun}"
            case "inspect":
                if result.session is None:
                    message = "Bash session inspected"
                else:
                    message = (
                        f"Session {result.session.session_id} is "
                        f"{result.session.status}"
                    )
            case "kill":
                if result.session is None:
                    message = result.message or "Bash session killed"
                else:
                    message = f"Killed bash session {result.session.session_id}"
            case "reset":
                count = len(result.sessions)
                noun = "session" if count == 1 else "sessions"
                message = f"Reset bash sessions; stopped {count} {noun}"
        return ToolResultDisplay(success=True, message=message)

    @classmethod
    def get_status_text(cls) -> str:
        return "Managing bash sessions"

    async def run(
        self, args: BashSessionsArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BashSessionsResult, None]:
        _ = ctx
        max_bytes = args.max_bytes or self.config.max_inline_bytes
        try:
            match args.action:
                case "list":
                    sessions = await asyncio.to_thread(_manager().list_sessions)
                    yield BashSessionsResult(action=args.action, sessions=sessions)
                case "inspect":
                    if not args.session_id:
                        raise ManagedBashError("session_id is required for inspect")
                    info, chunk = await asyncio.to_thread(
                        _manager().inspect_session, args.session_id, max_bytes
                    )
                    yield BashSessionsResult(
                        action=args.action,
                        session=info,
                        output=chunk.output,
                        next_cursor=chunk.next_cursor,
                        truncated=chunk.truncated,
                    )
                case "kill":
                    if not args.session_id:
                        raise ManagedBashError("session_id is required for kill")
                    info = await asyncio.to_thread(_manager().kill, args.session_id)
                    yield BashSessionsResult(
                        action=args.action,
                        session=info,
                        message=f"killed {args.session_id}",
                    )
                case "reset":
                    killed = await asyncio.to_thread(
                        _manager().reset, clear_logs=args.clear_logs
                    )
                    yield BashSessionsResult(
                        action=args.action,
                        sessions=killed,
                        message=f"reset {len(killed)} running session(s)",
                    )
        except (ManagedBashError, ManagedBashBackendError) as exc:
            raise ToolError(str(exc)) from exc


class BashLogFile(
    _DescriptionOnlyPromptMixin,
    BaseTool[BashLogFileArgs, BashLogFileResult, BashLogFileConfig, BaseToolState],
    ToolUIData[BashLogFileArgs, BashLogFileResult],
):
    description: ClassVar[str] = "Read or annotate managed bash output files."

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return _experimental_bash_enabled(config)

    @classmethod
    def format_call_display(cls, args: BashLogFileArgs) -> ToolCallDisplay:
        target = args.session_id or args.relative_path or "bash log"
        match args.action:
            case "read":
                return ToolCallDisplay(summary=f"Reading bash log {target}")
            case "write":
                return ToolCallDisplay(summary=f"Writing bash log {target}")
            case "append":
                return ToolCallDisplay(summary=f"Appending bash log {target}")

    @classmethod
    def format_result_display(cls, result: BashLogFileResult) -> ToolResultDisplay:
        path = Path(result.path).name
        match result.action:
            case "read":
                message = f"Read bash log {path}"
                suffix = "truncated" if result.truncated else ""
            case "write":
                message = f"Wrote {result.bytes_written or 0} bytes to bash log {path}"
                suffix = ""
            case "append":
                message = (
                    f"Appended {result.bytes_written or 0} bytes to bash log {path}"
                )
                suffix = ""
        return ToolResultDisplay(success=True, message=message, suffix=suffix)

    @classmethod
    def get_status_text(cls) -> str:
        return "Working with bash log"

    def resolve_permission(self, args: BashLogFileArgs) -> PermissionContext | None:
        if args.action == "read":
            return PermissionContext(permission=ToolPermission.ALWAYS)
        return PermissionContext(permission=self.config.permission)

    async def run(
        self, args: BashLogFileArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BashLogFileResult, None]:
        _ = ctx
        max_bytes = args.max_bytes or self.config.max_inline_bytes
        try:
            path = _manager().resolve_log_path(
                session_id=args.session_id, relative_path=args.relative_path
            )
            if args.action == "read":
                chunk = await asyncio.to_thread(
                    _manager().read_log_file,
                    path,
                    offset=args.offset,
                    max_bytes=max_bytes,
                )
                yield BashLogFileResult(
                    action=args.action,
                    path=str(path),
                    content=chunk.output,
                    next_cursor=chunk.next_cursor,
                    truncated=chunk.truncated,
                )
                return

            if args.content is None:
                raise ManagedBashError("content is required for write and append")
            bytes_written = await asyncio.to_thread(
                _manager().write_log_file,
                path,
                action=args.action,
                content=args.content,
            )
            yield BashLogFileResult(
                action=args.action, path=str(path), bytes_written=bytes_written
            )
        except (ManagedBashError, ManagedBashBackendError) as exc:
            raise ToolError(str(exc)) from exc
