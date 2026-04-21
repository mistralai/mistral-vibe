from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import ClassVar, Literal, final
from uuid import uuid4

from pydantic import BaseModel, Field
from tree_sitter import Language, Node, Parser
import tree_sitter_bash as tsbash

from vibe.core.logger import logger
from vibe.core.tools.arity import build_session_pattern
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
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


@lru_cache(maxsize=1)
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
            if parts:
                commands.append(" ".join(parts))

        for child in node.children:
            find_commands(child)

    find_commands(tree.root_node)
    return commands


def _get_subprocess_encoding() -> str:
    if sys.platform == "win32":
        # Windows console uses OEM code page (e.g., cp850, cp1252)
        import ctypes

        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    return "utf-8"


def _get_shell_executable() -> str | None:
    if is_windows():
        return None
    return os.environ.get("SHELL")


def _get_base_env() -> dict[str, str]:
    base_env = {**os.environ, "CI": "true", "NONINTERACTIVE": "1", "NO_TTY": "1"}

    if is_windows():
        base_env["GIT_PAGER"] = "more"
        base_env["PAGER"] = "more"
    else:
        base_env["TERM"] = "dumb"
        base_env["DEBIAN_FRONTEND"] = "noninteractive"
        base_env["GIT_PAGER"] = "cat"
        base_env["PAGER"] = "cat"
        base_env["LESS"] = "-FX"
        base_env["LC_ALL"] = "en_US.UTF-8"

    return base_env


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return

    try:
        if sys.platform == "win32":
            try:
                subprocess_proc = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/F",
                    "/T",
                    "/PID",
                    str(proc.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await subprocess_proc.wait()
            except (FileNotFoundError, OSError):
                proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

        await proc.wait()
    except (ProcessLookupError, PermissionError, OSError):
        pass


_MAX_BACKGROUND_OUTPUT_BYTES = 1_000_000
"""Per-stream cap for background-process output on disk.

Background processes (dev servers, watchers) can produce unbounded output.
We tail the most recent bytes up to this cap so the session directory stays
manageable; callers reading via ``bash_output`` still see the newest output.
"""


@dataclass
class BackgroundProcess:
    """Live handle for a bash command started with ``run_in_background=True``.

    Held in memory on ``BashState`` so ``on_reset`` can terminate the process
    and its drain tasks when the session is cleared.  The authoritative view
    of status and accumulated output lives on disk under ``session_dir`` and
    is what the ``bash_output`` tool reads.
    """

    bash_id: str
    command: str
    proc: asyncio.subprocess.Process
    started_at: float
    stdout_path: Path
    stderr_path: Path
    metadata_path: Path
    stdout_task: asyncio.Task[None]
    stderr_task: asyncio.Task[None]
    wait_task: asyncio.Task[None]


def _background_dir(session_dir: Path) -> Path:
    return session_dir / "bash_processes"


def _write_background_metadata(
    metadata_path: Path,
    *,
    bash_id: str,
    pid: int,
    command: str,
    status: str,
    started_at: float,
    returncode: int | None = None,
    ended_at: float | None = None,
) -> None:
    payload = {
        "bash_id": bash_id,
        "pid": pid,
        "command": command,
        "status": status,
        "started_at": started_at,
        "ended_at": ended_at,
        "returncode": returncode,
    }
    try:
        metadata_path.write_text(json.dumps(payload, indent=2))
    except OSError:
        logger.exception(
            "Failed to write background bash metadata to %s", metadata_path
        )


async def _drain_stream_to_file(
    stream: asyncio.StreamReader | None, path: Path
) -> None:
    """Append ``stream`` contents to ``path`` until EOF, with a byte cap.

    The file is created up front so readers can tail it even before the
    first chunk arrives.  Once the cap is reached we keep draining the
    stream (to avoid back-pressuring the child) but stop writing, which
    gives a natural "head" view of the output.
    """
    if stream is None:
        return

    try:
        path.write_bytes(b"")
    except OSError:
        logger.exception("Failed to initialise background output file %s", path)
        return

    bytes_written = 0
    try:
        with path.open("ab", buffering=0) as fh:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                if bytes_written >= _MAX_BACKGROUND_OUTPUT_BYTES:
                    continue
                remaining = _MAX_BACKGROUND_OUTPUT_BYTES - bytes_written
                to_write = chunk if len(chunk) <= remaining else chunk[:remaining]
                try:
                    fh.write(to_write)
                except OSError:
                    logger.exception("Failed to write background output to %s", path)
                    return
                bytes_written += len(to_write)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Background output drain failed for %s", path)


def _get_default_allowlist() -> list[str]:
    common = ["cd", "echo", "git diff", "git log", "git status", "tree", "whoami"]

    if is_windows():
        return common + ["dir", "findstr", "more", "type", "ver", "where"]
    else:
        return common + [
            "cat",
            "file",
            "head",
            "ls",
            "pwd",
            "stat",
            "tail",
            "uname",
            "wc",
            "which",
        ]


def _get_default_denylist() -> list[str]:
    common = ["gdb", "pdb", "passwd"]

    if is_windows():
        return common + ["cmd /k", "powershell -NoExit", "pwsh -NoExit", "notepad"]
    else:
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
    else:
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


def _collect_outside_dirs(command_parts: list[str]) -> set[str]:
    """Collect parent directories referenced outside the workdir.

    Iterates file-manipulating commands (see _PATH_COMMANDS) and inspects
    their arguments as candidate paths. Skips flags (-r, --recursive) and
    chmod mode strings (+x). For any argument that resolves outside the current
    working directory, adds the parent directory (or the path itself when it is
    a directory) to the result set — suitable for building an OUTSIDE_DIRECTORY
    RequiredPermission.
    """
    dirs: set[str] = set()
    for part in command_parts:
        tokens = part.split()
        command = tokens[0] if tokens else None
        if not command or command not in _PATH_COMMANDS:
            continue
        for token in tokens[1:]:
            # Skip CLI flags like -r, --recursive
            if token.startswith("-"):
                continue
            # Skip chmod mode strings like +x, +rwx — they are not file paths
            if command == "chmod" and token.startswith("+"):
                continue
            # Only consider tokens that look like paths
            if not (
                token.startswith(os.sep)
                or token.startswith("~")
                or token.startswith(".")
                or os.sep in token
            ):
                continue
            if is_path_within_workdir(token):
                continue
            # Resolve relative / home-relative paths, then collect parent dir
            resolved = Path(token).expanduser()
            if not resolved.is_absolute():
                resolved = Path.cwd() / resolved
            resolved = resolved.resolve()
            # For a directory target use the dir itself; for a file use its parent
            parent = str(resolved) if resolved.is_dir() else str(resolved.parent)
            dirs.add(parent)
    return dirs


class BashToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    max_output_bytes: int = Field(
        default=16_000, description="Maximum bytes to capture from stdout and stderr."
    )
    default_timeout: int = Field(
        default=300, description="Default timeout for commands in seconds."
    )
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
    sensitive_patterns: list[str] = Field(
        default=["sudo"],
        description="Command prefixes that always ASK regardless of arity approval.",
    )


class BashArgs(BaseModel):
    command: str
    timeout: int | None = Field(
        default=None, description="Override the default command timeout."
    )
    run_in_background: bool = Field(
        default=False,
        description=(
            "Start the command as a background process and return immediately "
            "with a `bash_id` handle.  Use the `bash_output` tool to tail the "
            "output and check whether it has exited.  Intended for long-running "
            "processes (dev servers, test watchers, builds) that would otherwise "
            "block the agent.  Requires an active session directory."
        ),
    )


class BashResult(BaseModel):
    command: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    bash_id: str | None = Field(
        default=None,
        description=(
            "Set when the command was started with ``run_in_background=True``. "
            "Pass this id to the ``bash_output`` tool to tail the process."
        ),
    )
    background: bool = Field(
        default=False,
        description="True when the command was started in background mode.",
    )


class Bash(
    BaseTool[BashArgs, BashResult, BashToolConfig, BaseToolState],
    ToolUIData[BashArgs, BashResult],
):
    description: ClassVar[str] = "Run a one-off bash command and capture its output."

    def __init__(
        self, config_getter: Callable[[], BashToolConfig], state: BaseToolState
    ) -> None:
        super().__init__(config_getter, state)
        # Live handles for background processes started in this session.
        # Stored on the instance (not on the Pydantic state model) because
        # they carry non-serialisable subprocess handles and asyncio tasks;
        # the authoritative view lives on disk under the session directory.
        self._background_processes: dict[str, BackgroundProcess] = {}

    @classmethod
    def format_call_display(cls, args: BashArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"bash: {args.command}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, BashResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        return ToolResultDisplay(success=True, message=f"Ran {event.result.command}")

    @classmethod
    def get_status_text(cls) -> str:
        return "Running command"

    def resolve_permission(self, args: BashArgs) -> PermissionContext | None:  # noqa: PLR0911, PLR0912
        if is_windows():
            return None

        command_parts = _extract_commands(args.command)
        if not command_parts:
            return None

        def _matches_pattern(command: str, pattern: str) -> bool:
            return command == pattern or command.startswith(pattern + " ")

        def find_denylist_match(command: str) -> str | None:
            return next(
                (p for p in self.config.denylist if _matches_pattern(command, p)), None
            )

        def is_standalone_denylisted(command: str) -> bool:
            parts = command.split()
            if not parts:
                return False
            base_command = parts[0]
            if len(parts) == 1:
                command_name = os.path.basename(base_command)
                if command_name in self.config.denylist_standalone:
                    return True
                if base_command in self.config.denylist_standalone:
                    return True
            return False

        def is_allowlisted(command: str) -> bool:
            return any(
                _matches_pattern(command, pattern) for pattern in self.config.allowlist
            )

        def is_sensitive(command: str) -> bool:
            tokens = command.split()
            if not tokens:
                return False
            return tokens[0] in self.config.sensitive_patterns

        for part in command_parts:
            if matched := find_denylist_match(part):
                return PermissionContext(
                    permission=ToolPermission.NEVER,
                    reason=f"Command denied: '{part}' matches denylist pattern '{matched}'. Do not attempt to run this command.",
                )
            if is_standalone_denylisted(part):
                return PermissionContext(
                    permission=ToolPermission.NEVER,
                    reason=f"Command denied: '{part}' is not allowed as a standalone command. Do not attempt to run this command.",
                )

        if self.config.permission == ToolPermission.ALWAYS:
            return PermissionContext(permission=ToolPermission.ALWAYS)

        has_sensitive = any(is_sensitive(part) for part in command_parts)
        all_allowlisted = not has_sensitive and all(
            is_allowlisted(part) for part in command_parts
        )
        outside_dirs = _collect_outside_dirs(command_parts)

        if all_allowlisted and not outside_dirs:
            return PermissionContext(permission=ToolPermission.ALWAYS)

        required: list[RequiredPermission] = []
        seen_session: set[str] = set()

        for part in command_parts:
            if not part:
                continue
            tokens = part.split()
            if not tokens:
                continue
            if not is_sensitive(part) and is_allowlisted(part):
                continue

            if is_sensitive(part):
                required.append(
                    RequiredPermission(
                        scope=PermissionScope.COMMAND_PATTERN,
                        invocation_pattern=part,
                        session_pattern=part,
                        label=part,
                    )
                )
            else:
                session_pat = build_session_pattern(tokens)
                if session_pat not in seen_session:
                    seen_session.add(session_pat)
                    required.append(
                        RequiredPermission(
                            scope=PermissionScope.COMMAND_PATTERN,
                            invocation_pattern=part,
                            session_pattern=session_pat,
                            label=session_pat,
                        )
                    )

        if outside_dirs:
            globs = sorted(str(Path(d) / "*") for d in outside_dirs)
            for glob in globs:
                required.append(
                    RequiredPermission(
                        scope=PermissionScope.OUTSIDE_DIRECTORY,
                        invocation_pattern=glob,
                        session_pattern=glob,
                        label=f"outside workdir ({glob})",
                    )
                )

        if not required:
            return None

        return PermissionContext(
            permission=ToolPermission.ASK, required_permissions=required
        )

    @final
    def _build_timeout_error(self, command: str, timeout: int) -> ToolError:
        return ToolError(f"Command timed out after {timeout}s: {command!r}")

    @final
    def _build_result(
        self, *, command: str, stdout: str, stderr: str, returncode: int
    ) -> BashResult:
        if returncode != 0:
            error_msg = f"Command failed: {command!r}\n"
            error_msg += f"Return code: {returncode}"
            if stderr:
                error_msg += f"\nStderr: {stderr}"
            if stdout:
                error_msg += f"\nStdout: {stdout}"
            raise ToolError(error_msg.strip())

        return BashResult(
            command=command, stdout=stdout, stderr=stderr, returncode=returncode
        )

    async def run(
        self, args: BashArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BashResult, None]:
        if args.run_in_background:
            async for item in self._run_background(args, ctx):
                yield item
            return

        async for item in self._run_foreground(args):
            yield item

    async def _run_foreground(
        self, args: BashArgs
    ) -> AsyncGenerator[ToolStreamEvent | BashResult, None]:
        timeout = args.timeout or self.config.default_timeout
        max_bytes = self.config.max_output_bytes

        proc = None
        try:
            # start_new_session is Unix-only, on Windows it's ignored
            kwargs: dict[Literal["start_new_session"], bool] = (
                {} if is_windows() else {"start_new_session": True}
            )

            proc = await asyncio.create_subprocess_shell(
                args.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=_get_base_env(),
                executable=_get_shell_executable(),
                **kwargs,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                await _kill_process_tree(proc)
                raise self._build_timeout_error(args.command, timeout)

            encoding = _get_subprocess_encoding()
            stdout = (
                stdout_bytes.decode(encoding, errors="replace")[:max_bytes]
                if stdout_bytes
                else ""
            )
            stderr = (
                stderr_bytes.decode(encoding, errors="replace")[:max_bytes]
                if stderr_bytes
                else ""
            )

            returncode = proc.returncode or 0

            yield self._build_result(
                command=args.command,
                stdout=stdout,
                stderr=stderr,
                returncode=returncode,
            )

        except (ToolError, asyncio.CancelledError):
            raise
        except Exception as exc:
            raise ToolError(f"Error running command {args.command!r}: {exc}") from exc
        finally:
            if proc is not None:
                await _kill_process_tree(proc)

    async def _run_background(
        self, args: BashArgs, ctx: InvokeContext | None
    ) -> AsyncGenerator[ToolStreamEvent | BashResult, None]:
        if ctx is None or ctx.session_dir is None:
            raise ToolError(
                "Background bash requires an active session directory. "
                "run_in_background=True cannot be used outside a session."
            )

        bg_dir = _background_dir(ctx.session_dir)
        try:
            bg_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ToolError(
                f"Could not create background bash directory {bg_dir}: {exc}"
            ) from exc

        bash_id = uuid4().hex[:12]
        stdout_path = bg_dir / f"{bash_id}.stdout"
        stderr_path = bg_dir / f"{bash_id}.stderr"
        metadata_path = bg_dir / f"{bash_id}.json"
        started_at = time.time()

        # Pre-create output files so ``bash_output`` can tail them immediately,
        # even before the drain tasks have scheduled their first read.
        for p in (stdout_path, stderr_path):
            try:
                p.write_bytes(b"")
            except OSError as exc:
                raise ToolError(
                    f"Could not create background bash output file {p}: {exc}"
                ) from exc

        kwargs: dict[Literal["start_new_session"], bool] = (
            {} if is_windows() else {"start_new_session": True}
        )

        try:
            proc = await asyncio.create_subprocess_shell(
                args.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=_get_base_env(),
                executable=_get_shell_executable(),
                **kwargs,
            )
        except Exception as spawn_exc:
            raise ToolError(
                f"Failed to start background command {args.command!r}: {spawn_exc}"
            ) from spawn_exc

        _write_background_metadata(
            metadata_path,
            bash_id=bash_id,
            pid=proc.pid or -1,
            command=args.command,
            status="running",
            started_at=started_at,
        )

        stdout_task = asyncio.create_task(
            _drain_stream_to_file(proc.stdout, stdout_path)
        )
        stderr_task = asyncio.create_task(
            _drain_stream_to_file(proc.stderr, stderr_path)
        )

        async def _wait_and_finalize() -> None:
            try:
                await proc.wait()
            finally:
                # Wait for the drain tasks so the last bytes land on disk
                # before we flip the status to "exited".
                for drain in (stdout_task, stderr_task):
                    try:
                        await drain
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.exception(
                            "Background output drain task failed for bash_id=%s",
                            bash_id,
                        )
                _write_background_metadata(
                    metadata_path,
                    bash_id=bash_id,
                    pid=proc.pid or -1,
                    command=args.command,
                    status="exited",
                    started_at=started_at,
                    returncode=proc.returncode,
                    ended_at=time.time(),
                )
                self._background_processes.pop(bash_id, None)

        wait_task = asyncio.create_task(_wait_and_finalize())

        self._background_processes[bash_id] = BackgroundProcess(
            bash_id=bash_id,
            command=args.command,
            proc=proc,
            started_at=started_at,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            metadata_path=metadata_path,
            stdout_task=stdout_task,
            stderr_task=stderr_task,
            wait_task=wait_task,
        )

        yield BashResult(
            command=args.command,
            stdout="",
            stderr="",
            returncode=0,
            bash_id=bash_id,
            background=True,
        )

    async def on_reset(self) -> None:
        """Terminate all background processes started in this session.

        Called by ``ToolManager.reset_all`` during history clears and at any
        point the tool cache is rebuilt.  Each managed process has its drain
        and wait tasks cancelled, then the process tree is killed and the
        metadata file updated so external readers (e.g. a later ``bash_output``
        call with the same id) see a stable ``terminated`` state.
        """
        procs = list(self._background_processes.values())
        self._background_processes.clear()
        for bp in procs:
            for task in (bp.stdout_task, bp.stderr_task, bp.wait_task):
                task.cancel()
            try:
                await _kill_process_tree(bp.proc)
            except Exception:
                logger.exception("Error terminating background bash %s", bp.bash_id)
            _write_background_metadata(
                bp.metadata_path,
                bash_id=bp.bash_id,
                pid=bp.proc.pid or -1,
                command=bp.command,
                status="terminated",
                started_at=bp.started_at,
                returncode=bp.proc.returncode,
                ended_at=time.time(),
            )
