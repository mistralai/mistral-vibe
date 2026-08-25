from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import AsyncGenerator, AsyncIterator, Collection
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
import errno
import functools
import json
import os
from pathlib import Path
import shlex
import threading
import time
from typing import TYPE_CHECKING, Any, ClassVar, Literal, get_args
import uuid

from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    JsonValue,
    computed_field,
    model_validator,
)
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
from vibe.core.tools.builtins.managed_shell import backend as managed_shell_backend
from vibe.core.tools.builtins.managed_shell.backend import (
    UNKNOWN_EXIT_CODE,
    ManagedShellBackend,
    ManagedShellBackendError,
    ManagedTerminal,
)
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import ToolPath, is_path_within_workdir, resolve_tool_path
from vibe.core.types import ToolResultEvent, ToolStreamEvent
from vibe.core.utils import is_windows
from vibe.core.workspace import Workspace
from vibe.observability.logging import logger
from vibe.utils.io import decode_console_safe
from vibe.utils.tool_presentation import ToolEffectKind

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.config.harness_files import HarnessFilesManager

Status = Literal["running", "completed", "killed", "timed_out", "orphaned"]
LogAction = Literal["read", "write", "append"]
SessionAction = Literal["list", "inspect", "kill", "reset"]

DEFAULT_INLINE_BYTES = 30_000
DEFAULT_MAX_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_POLL_SECONDS = 300.0
KILL_GRACE_SECONDS = 2.0
FORCE_TERMINATION_TIMEOUT_SECONDS = 2.0
READER_SELECT_SECONDS = 0.1
FOREGROUND_STREAM_SECONDS = 0.2

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


class ManagedShellError(Exception):
    pass


class SessionNotFoundError(ManagedShellError):
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


def posix_read_only_commands() -> list[str]:
    return list(_READ_ONLY_COMMANDS_POSIX)


def posix_shell_default_allowlist() -> list[str]:
    common = ["cd", "echo", "git diff", "git log", "git status", "tree", "whoami"]
    return common + posix_read_only_commands()


def posix_shell_default_denylist() -> list[str]:
    common = ["gdb", "pdb", "passwd"]
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


def posix_shell_default_denylist_standalone() -> list[str]:
    common = ["python", "python3", "ipython"]
    return common + ["bash", "sh", "nohup", "vi", "vim", "emacs", "nano", "su"]


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


_MUTATING_PATH_COMMANDS = {"cd", "chmod", "chown", "cp", "mkdir", "mv", "rm", "touch"}
_PATH_COMMANDS = _MUTATING_PATH_COMMANDS | set(_READ_ONLY_COMMANDS_POSIX)

_FIND_EXECUTION_PREDICATES = {"-exec", "-execdir", "-ok", "-okdir"}


def _split_command_tokens(
    command: str, *, preserve_backslashes: bool = False
) -> list[str]:
    try:
        if preserve_backslashes:
            lexer = shlex.shlex(command, posix=True)
            lexer.whitespace_split = True
            lexer.escape = ""
            return list(lexer)
        return shlex.split(command)
    except ValueError:
        return command.split()


def _looks_like_path(token: str) -> bool:
    return (
        token.startswith(os.sep)
        or token.startswith("~")
        or token.startswith(".")
        or "/" in token
        or "\\" in token
    )


def _collect_outside_dirs(
    command_parts: list[str],
    *,
    command_cwd: Path,
    workspace: Workspace,
    scratchpad_dir: Path | None,
    path_commands: Collection[str] = _PATH_COMMANDS,
    case_sensitive_commands: bool = True,
    preserve_backslashes: bool = False,
) -> set[str]:
    dirs: set[str] = set()
    if not is_path_within_workdir(
        str(command_cwd), workspace=workspace
    ) and not is_scratchpad_path(str(command_cwd), scratchpad_dir=scratchpad_dir):
        dirs.add(str(command_cwd))

    for part in command_parts:
        tokens = _split_command_tokens(part, preserve_backslashes=preserve_backslashes)
        command = tokens[0] if tokens else None
        if not command:
            continue
        command_name = command if case_sensitive_commands else command.lower()
        if command_name not in path_commands:
            continue
        for token in tokens[1:]:
            if token.startswith("-"):
                continue
            if command_name == "chmod" and token.startswith("+"):
                continue
            if not _looks_like_path(token):
                continue

            resolved = resolve_tool_path(token, command_cwd)

            if is_path_within_workdir(str(resolved), workspace=workspace):
                continue
            if is_scratchpad_path(str(resolved), scratchpad_dir=scratchpad_dir):
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
        raise ManagedShellError(f"invalid bytes_base64 value: {exc}") from exc


@dataclass
class TerminalSession:
    session_id: str
    command: str
    cwd: Path
    shell: str
    terminal: ManagedTerminal
    output_path: Path
    manifest_path: Path
    created_at: float
    pty_backend: str | None = None
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
    pty_backend: str | None = None
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
    # Verbatim, including CRLF: rewriting newlines would depend on where the window ends.
    return decode_console_safe(raw)


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


def _output_detail(output: str) -> str | None:
    # The model needs the output spelled out; the client already streamed it.
    return f"Output:\n{output}" if output else None


def _clip_to_bytes(text: str, max_bytes: int) -> str:
    raw = text.encode()
    if len(raw) <= max_bytes:
        return text
    # A clip lands up to 3 bytes short of max_bytes to keep the last character whole.
    return _trim_incomplete_utf8_suffix(raw[:max_bytes]).decode()


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
    def __init__(
        self,
        backend: ManagedShellBackend | None = None,
        *,
        shell_family: str = "posix",
        session_prefix: str = "bash",
    ) -> None:
        self._backend = backend or managed_shell_backend.create_managed_shell_backend(
            shell_family
        )
        self.shell_family = shell_family
        self.session_prefix = session_prefix
        self.base_dir = VIBE_HOME.path / "shell-tool"
        self.sessions_dir = self.base_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, TerminalSession] = {}
        self._orphaned: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._load_orphaned_manifests()

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
            raise ManagedShellError("command must not be empty")
        if not cwd.is_dir():
            raise ManagedShellError(f"cwd is not a directory: {cwd}")

        merged_env = self._build_env(env)

        session_id = self._new_session_id()
        output_path = self.sessions_dir / f"{session_id}.log"
        manifest_path = self.sessions_dir / f"{session_id}.json"
        output_path.touch()

        terminal = self._backend.start_terminal(
            shell=shell, command=command, cwd=cwd, env=merged_env
        )

        session = TerminalSession(
            session_id=session_id,
            command=command,
            cwd=cwd,
            shell=shell,
            terminal=terminal,
            output_path=output_path,
            manifest_path=manifest_path,
            created_at=time.time(),
            pty_backend=terminal.pty_backend,
        )
        reader = threading.Thread(
            target=self._reader_loop,
            args=(session,),
            name=f"managed-shell-reader-{session_id}",
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

    def write_stdin(self, session_id: str, text: str) -> int:
        return self.write_bytes(session_id, text.encode("utf-8"))

    def write_bytes(self, session_id: str, data: bytes) -> int:
        if not data:
            raise ManagedShellError("stdin payload must not be empty")
        session = self._live_session(session_id)
        with session.condition:
            if session.status != "running":
                raise ManagedShellError(
                    f"cannot write to session {session_id}; status is {session.status}"
                )
            try:
                return session.terminal.write(data)
            except (EOFError, OSError) as exc:
                raise ManagedShellError(f"failed to write stdin: {exc}") from exc

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
                more_output_expected=info.status == "running",
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
            more_output_expected=info.status == "running",
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
            raise ManagedShellError(f"invalid terminal kill status: {status}")

        session = self._live_session(session_id)
        with session.condition:
            if session.status != "running":
                return self._session_info_locked(session)
            session.status = status
            session.updated_at = time.time()
            session.condition.notify_all()

        try:
            self._terminate_sessions([session])
        except ManagedShellBackendError:
            self._restore_running_status([session])
            raise
        self._join_reader_threads([session])

        with session.condition:
            self._refresh_session_locked(session)
            self._save_manifest(session)
            return self._session_info_locked(session)

    def reset(self, *, clear_logs: bool) -> list[SessionInfo]:
        with self._lock:
            sessions = list(self._sessions.values())
            running_sessions: list[TerminalSession] = []
            for session in sessions:
                with session.condition:
                    self._refresh_session_locked(session)
                    if session.status != "running":
                        continue
                    session.status = "killed"
                    session.updated_at = time.time()
                    session.condition.notify_all()
                    running_sessions.append(session)

            try:
                self._terminate_sessions(running_sessions)
            except ManagedShellBackendError:
                self._restore_running_status(running_sessions)
                raise
            self._join_reader_threads(running_sessions)
            killed: list[SessionInfo] = []
            for session in running_sessions:
                with session.condition:
                    self._save_manifest(session)
                    killed.append(self._session_info_locked(session))

            if clear_logs:
                self._sessions.clear()
                self._orphaned.clear()
                for child in self.sessions_dir.glob(f"{self.session_prefix}_*"):
                    if child.is_file():
                        child.unlink(missing_ok=True)
        return killed

    def resolve_log_path(
        self, *, session_id: str | None, relative_path: str | None
    ) -> Path:
        if session_id:
            return Path(self.info(session_id).output_path)
        if not relative_path:
            raise ManagedShellError("provide either session_id or relative_path")
        candidate = (self.base_dir / relative_path).resolve()
        base = self.base_dir.resolve()
        if not candidate.is_relative_to(base):
            raise ManagedShellError("log path must stay under ~/.vibe/shell-tool")
        self._reject_other_family_session_log(candidate)
        return candidate

    def read_log_file(self, path: Path, *, offset: int, max_bytes: int) -> OutputChunk:
        return self._read_file_chunk(
            path,
            cursor=offset,
            max_bytes=max_bytes,
            more_output_expected=self._is_running_output_path(path),
        )

    def write_log_file(self, path: Path, *, action: LogAction, content: str) -> int:
        resolved = path.resolve()
        base = self.base_dir.resolve()
        if not resolved.is_relative_to(base):
            raise ManagedShellError("log path must stay under ~/.vibe/shell-tool")
        self._reject_other_family_session_log(resolved)
        self._reject_live_session_log_write(resolved)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        if action == "write":
            resolved.write_text(content, encoding="utf-8")
        elif action == "append":
            with resolved.open("a", encoding="utf-8") as handle:
                handle.write(content)
        else:
            raise ManagedShellError(f"unsupported write action: {action}")
        return len(content.encode("utf-8"))

    def _reject_live_session_log_write(self, path: Path) -> None:
        with self._lock:
            for session in self._sessions.values():
                with session.condition:
                    if session.output_path.resolve() != path:
                        continue
                    self._refresh_session_locked(session)
                    if session.status == "running":
                        raise ManagedShellError(
                            "cannot write or append to a live session log; use "
                            f"{self.session_prefix}_stdin for process input or wait "
                            "until the session exits"
                        )
                    return

    def _reject_other_family_session_log(self, path: Path) -> None:
        sessions_dir = self.sessions_dir.resolve()
        if not path.is_relative_to(sessions_dir):
            return
        if path.name.startswith(f"{self.session_prefix}_"):
            return
        raise ManagedShellError(
            f"log path must reference a {self.session_prefix} session file"
        )

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
                readable = session.terminal.wait_readable(READER_SELECT_SECONDS)
                if readable:
                    try:
                        chunk = session.terminal.read(8192)
                    except OSError as exc:
                        if exc.errno in {errno.EBADF, errno.EIO}:
                            break
                        raise
                    if not chunk:
                        break
                    self._append_output(session, chunk)

                if session.terminal.poll() is not None and not readable:
                    break
        except Exception as exc:
            with session.condition:
                session.reader_error = str(exc)
                session.updated_at = time.time()
                session.condition.notify_all()
        finally:
            if session.terminal.poll() is None:
                try:
                    self._terminate_sessions([session], force=True)
                except ManagedShellBackendError:
                    pass

            with session.condition:
                if session.status == "running":
                    session.status = "completed"
                # The terminate above can leave the terminal unreaped.
                returncode = session.terminal.returncode
                session.exit_code = (
                    UNKNOWN_EXIT_CODE if returncode is None else returncode
                )
                session.updated_at = time.time()
                session.condition.notify_all()
                self._save_manifest(session)

            try:
                session.terminal.close()
            except OSError:
                pass

    def _append_output(self, session: TerminalSession, data: bytes) -> None:
        with session.condition:
            with session.output_path.open("ab") as handle:
                handle.write(data)
            session.updated_at = time.time()
            session.condition.notify_all()

    def _terminate_sessions(
        self, sessions: list[TerminalSession], *, force: bool = False
    ) -> None:
        live = [session for session in sessions if session.terminal.poll() is None]
        if not live:
            return

        force_targets = live if force else []
        if not force:
            graceful: list[TerminalSession] = []
            for session in live:
                try:
                    self._backend.request_termination(session.terminal)
                except Exception:
                    force_targets.append(session)
                else:
                    graceful.append(session)
            force_targets.extend(
                self._wait_for_terminal_exit(graceful, KILL_GRACE_SECONDS)
            )

        force_errors: dict[str, Exception] = {}
        for session in force_targets:
            try:
                self._backend.force_terminate_terminal(
                    session.terminal, timeout_seconds=FORCE_TERMINATION_TIMEOUT_SECONDS
                )
            except Exception as exc:
                force_errors[session.session_id] = exc

        remaining = self._wait_for_terminal_exit(
            force_targets, FORCE_TERMINATION_TIMEOUT_SECONDS
        )
        if not remaining:
            return

        details = []
        for session in remaining:
            error = force_errors.get(session.session_id)
            detail = f"{session.session_id}: {error}" if error else session.session_id
            details.append(detail)
        raise ManagedShellBackendError(
            "failed to terminate managed shell process tree: " + ", ".join(details)
        )

    @staticmethod
    def _restore_running_status(sessions: list[TerminalSession]) -> None:
        for session in sessions:
            if session.terminal.poll() is not None:
                continue
            with session.condition:
                if session.status == "running":
                    continue
                session.status = "running"
                session.updated_at = time.time()
                session.condition.notify_all()

    @staticmethod
    def _wait_for_terminal_exit(
        sessions: list[TerminalSession], timeout_seconds: float
    ) -> list[TerminalSession]:
        deadline = time.monotonic() + timeout_seconds
        remaining = sessions
        while remaining:
            remaining = [
                session for session in remaining if session.terminal.poll() is None
            ]
            now = time.monotonic()
            if not remaining or now >= deadline:
                return remaining
            time.sleep(min(READER_SELECT_SECONDS, deadline - now))
        return []

    @staticmethod
    def _join_reader_threads(sessions: list[TerminalSession]) -> None:
        deadline = time.monotonic() + FORCE_TERMINATION_TIMEOUT_SECONDS
        for session in sessions:
            reader = session.reader_thread
            if reader is None:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            reader.join(timeout=remaining)

    def _refresh_session_locked(self, session: TerminalSession) -> None:
        if session.status != "running":
            return
        returncode = session.terminal.poll()
        if returncode is None:
            return
        # The process exited, but the reader thread may still be draining
        # buffered PTY output. Let it own the "completed" transition (see
        # _reader_loop's finally) so callers never observe a completed session
        # with output still in flight.
        reader = session.reader_thread
        if reader is not None and reader.is_alive():
            return
        session.status = "completed"
        session.exit_code = returncode
        session.updated_at = time.time()
        session.condition.notify_all()
        self._save_manifest(session)

    def _build_env(self, overrides: dict[str, str] | None) -> dict[str, str]:
        env = dict(os.environ)
        match self.shell_family:
            case "powershell" | "windows":
                env.update({"GIT_PAGER": "more", "PAGER": "more"})
            case "posix" | "git_bash":
                env.update({
                    "TERM": env.get("TERM", "xterm-256color"),
                    "COLUMNS": env.get("COLUMNS", "120"),
                    "LINES": env.get("LINES", "40"),
                    # Keep the PTY interactive (so stdin can drive REPLs/prompts)
                    # while neutralising pagers such as `less`.
                    "GIT_PAGER": "cat",
                    "PAGER": "cat",
                    "LESS": "-FX",
                    "DEBIAN_FRONTEND": "noninteractive",
                })
            case _:
                raise ManagedShellError(
                    f"unknown managed shell family: {self.shell_family}"
                )
        if overrides:
            env.update(overrides)
        return env

    def _live_session(self, session_id: str) -> TerminalSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            if session_id in self._orphaned:
                raise ManagedShellError(f"session is orphaned: {session_id}") from exc
            raise SessionNotFoundError(
                f"unknown {self.session_prefix} session: {session_id}"
            ) from exc

    def _orphan_info(self, session_id: str) -> SessionInfo:
        try:
            return self._info_from_manifest(self._orphaned[session_id])
        except KeyError as exc:
            raise SessionNotFoundError(
                f"unknown {self.session_prefix} session: {session_id}"
            ) from exc

    def _session_info_locked(self, session: TerminalSession) -> SessionInfo:
        return SessionInfo(
            session_id=session.session_id,
            command=session.command,
            cwd=str(session.cwd),
            shell=session.shell,
            pty_backend=session.pty_backend,
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
            if not session_id.startswith(f"{self.session_prefix}_"):
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
        more_output_expected: bool = False,
    ) -> OutputChunk:
        if cursor < 0:
            raise ManagedShellError("cursor must be a non-negative byte offset")
        if max_bytes <= 0:
            raise ManagedShellError("max_bytes must be a positive byte limit")
        if not path.exists():
            return OutputChunk(output="", next_cursor=cursor, truncated=False)

        size = _safe_stat_size(path)
        safe_cursor = min(cursor, size)
        with path.open("rb") as handle:
            handle.seek(safe_cursor)
            raw = handle.read(max_bytes)
        if more_output_expected or size > safe_cursor + len(raw):
            raw = _trim_incomplete_utf8_suffix(raw)
        next_cursor = safe_cursor + len(raw)
        return OutputChunk(
            output=_decode_output(raw),
            next_cursor=next_cursor,
            truncated=size > next_cursor,
        )

    def _new_session_id(self) -> str:
        stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        return f"{self.session_prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


class _ForegroundStream:
    """Drained before any result is yielded, so "streamed" means "streamed whole"."""

    def __init__(
        self,
        manager: TerminalSessionManager,
        *,
        session_id: str,
        tool_name: str,
        tool_call_id: str | None,
        max_bytes: int,
    ) -> None:
        self._manager = manager
        self._session_id = session_id
        self._tool_name = tool_name
        self._tool_call_id = tool_call_id
        # Caps both a single read and the total; the result is read under it too.
        self._max_bytes = max_bytes
        self._budget = max_bytes
        self._cursor = 0
        self.completed = False

    def _event(self, output: str) -> ToolStreamEvent | None:
        if self._tool_call_id is None or self._budget <= 0:
            return None
        message = _clip_to_bytes(output, self._budget)
        if not message:
            return None
        self._budget -= len(message.encode())
        return ToolStreamEvent(
            tool_name=self._tool_name, tool_call_id=self._tool_call_id, message=message
        )

    async def pump(self, *, timeout: float) -> AsyncGenerator[ToolStreamEvent, None]:
        deadline = time.monotonic() + timeout
        while not self.completed:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                return
            info, chunk = await asyncio.to_thread(
                self._manager.read_output,
                session_id=self._session_id,
                cursor=self._cursor,
                wait_seconds=min(remaining_seconds, FOREGROUND_STREAM_SECONDS),
                max_bytes=self._max_bytes,
            )
            self._cursor = chunk.next_cursor
            if event := self._event(chunk.output):
                yield event
            # A terminal status is published only after the reader's last write.
            self.completed = info.status != "running"

    async def drain(self) -> ToolStreamEvent | None:
        info, chunk = await asyncio.to_thread(
            self._manager.read_output,
            session_id=self._session_id,
            cursor=self._cursor,
            wait_seconds=0,
            max_bytes=self._max_bytes,
        )
        self._cursor = chunk.next_cursor
        self.completed = info.status != "running"
        return self._event(chunk.output)


def _manager(
    tool: BaseTool, *, shell_family: str = "posix", session_prefix: str = "bash"
) -> TerminalSessionManager:
    return tool.terminal_runtime.get(
        shell_family=shell_family, session_prefix=session_prefix
    )


def _experimental_bash_enabled(config: VibeConfigSchema | None) -> bool:
    _ = config
    return not is_windows() and managed_shell_backend.managed_shell_supported("posix")


class ExperimentalBashToolConfig(BashToolConfig):
    allowlist: list[str] = Field(
        default_factory=_get_default_allowlist,
        description="Command prefixes that are automatically allowed",
    )
    denylist: list[str] = Field(
        default_factory=_get_default_denylist,
        description="Command prefixes that are automatically denied",
    )
    denylist_standalone: list[str] = Field(
        default_factory=_get_default_denylist_standalone,
        description="Commands that are denied only when run without arguments",
    )
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
    cwd: ToolPath | None = Field(
        default=None, description="Working directory override."
    )
    env: dict[str, str] | None = Field(
        default=None, description="Environment variable overrides."
    )
    shell: str | None = Field(default=None, description="Shell executable override.")


class ExperimentalBashResult(BaseModel):
    command: str
    session_id: str = ""
    status: Status = "completed"
    exit_code: int | None = None
    shell: str = ""
    background: bool = False
    # The PTY interleaves both streams here, so there is no separate stderr.
    output: str = ""
    next_cursor: int = 0
    truncated: bool = False
    output_path: str = ""

    # Kept for `post_tool` hooks that read `tool_output.returncode`; the dumped
    # result is the hook payload verbatim.
    @computed_field(description="Deprecated alias for `exit_code`.")
    @property
    def returncode(self) -> int:
        return self.exit_code or 0


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
    action: SessionAction = Field(
        default="list",
        description=(
            "`list` lists this tool family's sessions. `inspect` requires "
            "`session_id` and reads one session. `kill` requires `session_id` "
            "and terminates exactly that one session. `reset` ignores "
            "`session_id` and stops every session in this tool family."
        ),
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "Required for `inspect` and `kill`; ignored for `list` and `reset`. "
            "`kill` affects exactly this one session."
        ),
    )
    clear_logs: bool = Field(
        default=False,
        description="Only used with `reset`; when true, also delete stored logs.",
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

        cwd: Path
        harness_files: HarnessFilesManager
        workspace: Workspace
        scratchpad_dir: Path | None

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

    def _resolve_posix_shell_permission(
        self,
        *,
        command: str,
        cwd: str | None,
        required_context_permissions: list[RequiredPermission] | None = None,
    ) -> PermissionContext | None:
        command_parts = _extract_commands(command)
        if not command_parts:
            return None

        guardrail_permission = self._resolve_guardrail_permission(command_parts)
        if (
            guardrail_permission
            and guardrail_permission.permission == ToolPermission.NEVER
        ):
            return guardrail_permission

        command_cwd = resolve_tool_path(cwd, self.cwd)
        outside_dirs = _collect_outside_dirs(
            command_parts,
            command_cwd=command_cwd,
            workspace=self.workspace,
            scratchpad_dir=self.scratchpad_dir,
        )
        context_required = required_context_permissions or []
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
    effect_kind = ToolEffectKind.SHELL
    description: ClassVar[str] = "Run a shell command in a managed PTY session."
    selection_priority: ClassVar[int] = 10
    shell_rollout: ClassVar[str | None] = "managed"
    local_managed_shell_only: ClassVar[bool] = True
    shell_family: ClassVar[str] = "posix"
    session_prefix: ClassVar[str] = "bash"

    @classmethod
    def get_name(cls) -> str:
        return "bash"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _experimental_bash_enabled(config)

    @classmethod
    def format_call_display(cls, args: ExperimentalBashArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"bash: {args.command}",
            verb="Running",
            message=args.command,
            settled_verb="Ran",
            settled_message=args.command,
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, ExperimentalBashResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        status = event.result.status
        verb = "Running" if status == "running" else "Ran"
        return ToolResultDisplay(
            success=event.error is None, verb=verb, message=event.result.command
        )

    @classmethod
    def project_result(cls, result: ExperimentalBashResult) -> JsonValue:
        # The PTY interleaves both streams, so the transcript is the whole output.
        return {
            "stdout": result.output,
            "stderr": "",
            "output": result.output,
            "truncated": result.truncated,
        }

    @classmethod
    def get_status_text(cls) -> str:
        return "Running command"

    def _session_manager(self) -> TerminalSessionManager:
        return _manager(
            self, shell_family=self.shell_family, session_prefix=self.session_prefix
        )

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

        return self._resolve_posix_shell_permission(
            command=args.command,
            cwd=args.cwd,
            required_context_permissions=self._build_context_permissions(args),
        )

    async def run(
        self, args: ExperimentalBashArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ExperimentalBashResult, None]:
        requested_timeout = (
            float(args.timeout) if args.timeout is not None else args.timeout_seconds
        )
        hard_timeout = args.hard_timeout or args.timeout is not None
        timeout = self._resolve_timeout(requested_timeout)
        max_bytes = self.config.max_output_bytes
        try:
            cwd = resolve_tool_path(args.cwd, self.cwd)
            manager = self._session_manager()
            shell = manager.resolve_shell(args.shell, self.config.shell)
            session = await asyncio.to_thread(
                manager.start,
                command=args.command,
                cwd=cwd,
                env=args.env,
                shell=shell,
                background=args.background,
            )
            if args.background:
                yield await self._result_from_session(
                    session.session_id, True, max_bytes
                )
                return

            stream = _ForegroundStream(
                manager,
                session_id=session.session_id,
                tool_name=self.get_name(),
                tool_call_id=ctx.tool_call_id if ctx is not None else None,
                max_bytes=max_bytes,
            )
            # The session is ours until it exits or is handed over to the background.
            async with self._kill_on_abort(session.session_id):
                async for event in stream.pump(timeout=timeout):
                    yield event
                if event := await stream.drain():
                    yield event

            if stream.completed:
                yield await self._result_from_session(
                    session.session_id,
                    background=False,
                    max_bytes=max_bytes,
                    enforce_success=True,
                )
                return

            if not hard_timeout:
                yield await self._result_from_session(
                    session.session_id, True, max_bytes
                )
                return

            info = await asyncio.to_thread(
                manager.kill, session.session_id, status="timed_out"
            )
            # The kill can push out a last line after the drain above.
            if event := await stream.drain():
                yield event
            chunk = await asyncio.to_thread(
                manager.read_log_file,
                Path(info.output_path),
                offset=0,
                max_bytes=max_bytes,
            )
            reason = (
                "Command timed out after "
                f"{timeout:g}s: {args.command!r}\n"
                f"session_id: {info.session_id}\n"
                f"status: {info.status}\n"
                f"output_path: {info.output_path}"
            )
            raise ToolError(reason, model_detail=_output_detail(chunk.output))
        except ToolError:
            raise
        except (ManagedShellError, ManagedShellBackendError) as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"Error running command {args.command!r}: {exc}") from exc

    @contextlib.asynccontextmanager
    async def _kill_on_abort(self, session_id: str) -> AsyncIterator[None]:
        try:
            yield
        except (asyncio.CancelledError, GeneratorExit):
            try:
                # Shielded: a second cancellation must not leave the PTY running.
                await asyncio.shield(
                    asyncio.to_thread(self._session_manager().kill, session_id)
                )
            except Exception:
                logger.warning(
                    "Failed to kill managed shell session %s after cancellation",
                    session_id,
                    exc_info=True,
                )
            raise

    def _resolve_timeout(self, requested: float | None) -> float:
        timeout = self.config.default_timeout if requested is None else requested
        return min(timeout, self.config.max_timeout_seconds)

    async def _result_from_session(
        self,
        session_id: str,
        background: bool,
        max_bytes: int,
        *,
        enforce_success: bool = False,
    ) -> ExperimentalBashResult:
        manager = self._session_manager()
        info, chunk = await asyncio.to_thread(
            manager.read_output,
            session_id=session_id,
            cursor=0,
            wait_seconds=0,
            max_bytes=max_bytes,
        )
        returncode = info.exit_code or 0
        if enforce_success and (info.status != "completed" or returncode != 0):
            reason = f"Command failed: {info.command!r}\nReturn code: {returncode}"
            if info.status != "completed":
                reason += f"\nStatus: {info.status}"
            raise ToolError(reason, model_detail=_output_detail(chunk.output))

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
            shell=info.shell,
        )


class BashOutput(
    _DescriptionOnlyPromptMixin,
    BaseTool[BashOutputArgs, BashOutputResult, BashOutputConfig, BaseToolState],
    ToolUIData[BashOutputArgs, BashOutputResult],
):
    description: ClassVar[str] = "Poll output from a running or completed bash session."
    shell_rollout: ClassVar[str | None] = "managed"
    local_managed_shell_only: ClassVar[bool] = True
    shell_family: ClassVar[str] = "posix"
    session_prefix: ClassVar[str] = "bash"
    session_label: ClassVar[str] = "bash"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _experimental_bash_enabled(config)

    @classmethod
    def format_call_display(cls, args: BashOutputArgs) -> ToolCallDisplay:
        message = f"{cls.session_label} session {args.session_id}"
        if args.wait_seconds > 0:
            return ToolCallDisplay(
                summary=f"Waiting for {message}",
                verb="Waiting for",
                message=message,
                settled_verb="Polled",
                settled_message=message,
            )
        return ToolCallDisplay(
            summary=f"Polling {message}",
            verb="Polling",
            message=message,
            settled_verb="Polled",
            settled_message=message,
        )

    @classmethod
    def format_result_display(cls, result: BashOutputResult) -> ToolResultDisplay:
        match result.status:
            case "running":
                message = f"session {result.session_id} is still running"
            case "completed":
                message = f"session {result.session_id} completed"
            case "killed":
                message = f"session {result.session_id} was killed"
            case "timed_out":
                message = f"session {result.session_id} timed out"
            case "orphaned":
                message = f"session {result.session_id} is orphaned"
        suffix = "truncated" if result.truncated else ""
        return ToolResultDisplay(
            success=result.status in {"running", "completed", "orphaned"},
            verb="Polled",
            message=message,
            suffix=suffix,
        )

    @classmethod
    def get_status_text(cls) -> str:
        return f"Polling {cls.session_label} session"

    def _session_manager(self) -> TerminalSessionManager:
        return _manager(
            self, shell_family=self.shell_family, session_prefix=self.session_prefix
        )

    async def run(
        self, args: BashOutputArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BashOutputResult, None]:
        _ = ctx
        cursor = 0 if args.cursor is None else args.cursor
        wait_seconds = min(args.wait_seconds, self.config.max_poll_seconds)
        max_bytes = args.max_bytes or self.config.max_inline_bytes
        try:
            info, chunk = await asyncio.to_thread(
                self._session_manager().read_output,
                session_id=args.session_id,
                cursor=cursor,
                wait_seconds=wait_seconds,
                max_bytes=max_bytes,
            )
        except (ManagedShellError, ManagedShellBackendError) as exc:
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
    shell_rollout: ClassVar[str | None] = "managed"
    local_managed_shell_only: ClassVar[bool] = True
    shell_family: ClassVar[str] = "posix"
    session_prefix: ClassVar[str] = "bash"
    session_label: ClassVar[str] = "bash"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _experimental_bash_enabled(config)

    @classmethod
    def format_call_display(cls, args: BashStdinArgs) -> ToolCallDisplay:
        message = f"input to {cls.session_label} session {args.session_id}"
        return ToolCallDisplay(
            summary=f"Sending {message}",
            verb="Sending",
            message=message,
            settled_verb="Sent",
            settled_message=message,
        )

    @classmethod
    def format_result_display(cls, result: BashStdinResult) -> ToolResultDisplay:
        return ToolResultDisplay(
            success=result.status in {"running", "completed"},
            verb="Sent",
            message=(
                f"{result.bytes_written} bytes to "
                f"{result.status} session {result.session_id}"
            ),
        )

    @classmethod
    def get_status_text(cls) -> str:
        return f"Sending {cls.session_label} input"

    def _session_manager(self) -> TerminalSessionManager:
        return _manager(
            self, shell_family=self.shell_family, session_prefix=self.session_prefix
        )

    async def run(
        self, args: BashStdinArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BashStdinResult, None]:
        _ = ctx
        try:
            payload = self._build_payload(args)
            manager = self._session_manager()
            bytes_written = await asyncio.to_thread(
                manager.write_bytes, args.session_id, payload
            )
            info = manager.info(args.session_id)
        except (ManagedShellError, ManagedShellBackendError) as exc:
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
                raise ManagedShellError(
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
    shell_rollout: ClassVar[str | None] = "managed"
    local_managed_shell_only: ClassVar[bool] = True
    shell_family: ClassVar[str] = "posix"
    session_prefix: ClassVar[str] = "bash"
    session_label: ClassVar[str] = "bash"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _experimental_bash_enabled(config)

    @classmethod
    def format_call_display(cls, args: BashSessionsArgs) -> ToolCallDisplay:
        match args.action:
            case "list":
                return ToolCallDisplay(
                    summary=f"Listing {cls.session_label} sessions",
                    verb="Listing",
                    message=f"{cls.session_label} sessions",
                    settled_verb="Listed",
                    settled_message=f"{cls.session_label} sessions",
                )
            case "inspect":
                message = f"{cls.session_label} session {args.session_id or ''}".strip()
                return ToolCallDisplay(
                    summary=f"Inspecting {message}",
                    verb="Inspecting",
                    message=message,
                    settled_verb="Inspected",
                    settled_message=message,
                )
            case "kill":
                message = (
                    f"one {cls.session_label} session {args.session_id or ''}"
                ).strip()
                return ToolCallDisplay(
                    summary=f"Killing {message}",
                    verb="Killing",
                    message=message,
                    settled_verb="Killed",
                    settled_message=message,
                )
            case "reset":
                message = f"all {cls.session_label} sessions"
                return ToolCallDisplay(
                    summary=f"Resetting {message}",
                    verb="Resetting",
                    message=message,
                    settled_verb="Reset",
                    settled_message=message,
                )

    @classmethod
    def format_result_display(cls, result: BashSessionsResult) -> ToolResultDisplay:
        match result.action:
            case "list":
                count = len(result.sessions)
                noun = "session" if count == 1 else "sessions"
                verb = "Listed"
                message = f"{count} {cls.session_label} {noun}"
            case "inspect":
                verb = "Inspected"
                if result.session is None:
                    message = f"{cls.session_label} session"
                else:
                    message = (
                        f"session {result.session.session_id} is "
                        f"{result.session.status}"
                    )
            case "kill":
                verb = "Killed"
                if result.session is None:
                    message = result.message or f"one {cls.session_label} session"
                else:
                    message = (
                        f"one {cls.session_label} session {result.session.session_id}"
                    )
            case "reset":
                count = len(result.sessions)
                noun = "session" if count == 1 else "sessions"
                verb = "Reset"
                message = f"all {cls.session_label} sessions; stopped {count} {noun}"
        return ToolResultDisplay(success=True, verb=verb, message=message)

    @classmethod
    def get_status_text(cls) -> str:
        return f"Managing {cls.session_label} sessions"

    def _session_manager(self) -> TerminalSessionManager:
        return _manager(
            self, shell_family=self.shell_family, session_prefix=self.session_prefix
        )

    async def run(
        self, args: BashSessionsArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BashSessionsResult, None]:
        _ = ctx
        max_bytes = args.max_bytes or self.config.max_inline_bytes
        try:
            manager = self._session_manager()
            match args.action:
                case "list":
                    sessions = await asyncio.to_thread(manager.list_sessions)
                    yield BashSessionsResult(action=args.action, sessions=sessions)
                case "inspect":
                    if not args.session_id:
                        raise ManagedShellError("session_id is required for inspect")
                    info, chunk = await asyncio.to_thread(
                        manager.inspect_session, args.session_id, max_bytes
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
                        raise ManagedShellError("session_id is required for kill")
                    info = await asyncio.to_thread(manager.kill, args.session_id)
                    yield BashSessionsResult(
                        action=args.action,
                        session=info,
                        message=f"killed {args.session_id}",
                    )
                case "reset":
                    killed = await asyncio.to_thread(
                        manager.reset, clear_logs=args.clear_logs
                    )
                    yield BashSessionsResult(
                        action=args.action,
                        sessions=killed,
                        message=f"reset {len(killed)} running session(s)",
                    )
        except (ManagedShellError, ManagedShellBackendError) as exc:
            raise ToolError(str(exc)) from exc


class BashLogFile(
    _DescriptionOnlyPromptMixin,
    BaseTool[BashLogFileArgs, BashLogFileResult, BashLogFileConfig, BaseToolState],
    ToolUIData[BashLogFileArgs, BashLogFileResult],
):
    description: ClassVar[str] = "Read or annotate managed bash output files."
    shell_rollout: ClassVar[str | None] = "managed"
    local_managed_shell_only: ClassVar[bool] = True
    shell_family: ClassVar[str] = "posix"
    session_prefix: ClassVar[str] = "bash"
    session_label: ClassVar[str] = "bash"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _experimental_bash_enabled(config)

    @classmethod
    def format_call_display(cls, args: BashLogFileArgs) -> ToolCallDisplay:
        target = args.session_id or args.relative_path or f"{cls.session_label} log"
        message = f"{cls.session_label} log {target}"
        match args.action:
            case "read":
                return ToolCallDisplay(
                    summary=f"Reading {message}",
                    verb="Reading",
                    message=message,
                    settled_verb="Read",
                    settled_message=message,
                )
            case "write":
                return ToolCallDisplay(
                    summary=f"Writing {message}",
                    verb="Writing",
                    message=message,
                    settled_verb="Wrote",
                    settled_message=message,
                )
            case "append":
                return ToolCallDisplay(
                    summary=f"Appending {message}",
                    verb="Appending",
                    message=message,
                    settled_verb="Appended",
                    settled_message=message,
                )

    @classmethod
    def format_result_display(cls, result: BashLogFileResult) -> ToolResultDisplay:
        path = Path(result.path).name
        match result.action:
            case "read":
                verb = "Read"
                message = f"{cls.session_label} log {path}"
                suffix = "truncated" if result.truncated else ""
            case "write":
                verb = "Wrote"
                message = (
                    f"{result.bytes_written or 0} bytes to "
                    f"{cls.session_label} log {path}"
                )
                suffix = ""
            case "append":
                verb = "Appended"
                message = (
                    f"{result.bytes_written or 0} bytes to "
                    f"{cls.session_label} log {path}"
                )
                suffix = ""
        return ToolResultDisplay(
            success=True, verb=verb, message=message, suffix=suffix
        )

    @classmethod
    def get_status_text(cls) -> str:
        return f"Working with {cls.session_label} log"

    def _session_manager(self) -> TerminalSessionManager:
        return _manager(
            self, shell_family=self.shell_family, session_prefix=self.session_prefix
        )

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
            manager = self._session_manager()
            path = manager.resolve_log_path(
                session_id=args.session_id, relative_path=args.relative_path
            )
            if args.action == "read":
                chunk = await asyncio.to_thread(
                    manager.read_log_file, path, offset=args.offset, max_bytes=max_bytes
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
                raise ManagedShellError("content is required for write and append")
            bytes_written = await asyncio.to_thread(
                manager.write_log_file, path, action=args.action, content=args.content
            )
            yield BashLogFileResult(
                action=args.action, path=str(path), bytes_written=bytes_written
            )
        except (ManagedShellError, ManagedShellBackendError) as exc:
            raise ToolError(str(exc)) from exc
