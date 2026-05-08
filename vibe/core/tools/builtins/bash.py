from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from functools import lru_cache
import os
from pathlib import Path
import sys
from typing import ClassVar, Literal, final

from pydantic import BaseModel, Field
from tree_sitter import Language, Node, Parser
import tree_sitter_bash as tsbash

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
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import is_path_within_workdir
from vibe.core.types import ToolResultEvent, ToolStreamEvent
from vibe.core.utils import is_windows, kill_async_subprocess


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


def _get_default_allowlist() -> list[str]:
    common = ["cd", "echo", "git diff", "git log", "git status", "tree", "whoami"]

    if is_windows():
        return common + ["dir", "findstr", "more", "type", "ver", "where"]
    else:
        return common + [
            "cat",
            "file",
            "find",
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

_FIND_EXECUTION_PREDICATES = {"-exec", "-execdir", "-ok", "-okdir"}


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
            if is_scratchpad_path(token):
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


def _matches_pattern(command: str, pattern: str) -> bool:
    return command == pattern or command.startswith(pattern + " ")


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


class BashResult(BaseModel):
    command: str
    stdout: str
    stderr: str
    returncode: int


class Bash(
    BaseTool[BashArgs, BashResult, BashToolConfig, BaseToolState],
    ToolUIData[BashArgs, BashResult],
):
    description: ClassVar[str] = "Run a one-off bash command and capture its output."

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

    @staticmethod
    def _has_find_execution_predicate(command: str) -> bool:
        """Defensive check for find -exec, -execdir, -ok, -okdir predicates."""
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
            (p for p in self.config.denylist if _matches_pattern(command, p)), None
        )

    def _is_standalone_denylisted(self, command: str) -> bool:
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
        self, command_parts: list[str], outside_dirs: set[str]
    ) -> bool:
        if any(self._is_sensitive(part) for part in command_parts):
            return False

        if self.config.permission == ToolPermission.ALWAYS:
            return True

        return all(self._is_allowlisted(part) for part in command_parts) and (
            not outside_dirs
        )

    def _build_required_permissions(
        self, command_parts: list[str], outside_dirs: set[str]
    ) -> list[RequiredPermission]:
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

            session_pat = build_session_pattern(tokens)
            if session_pat in seen_session:
                continue
            seen_session.add(session_pat)
            required.append(
                self._build_command_required_permission(
                    invocation_pattern=part,
                    session_pattern=session_pat,
                    label=session_pat,
                )
            )

        for glob in sorted(str(Path(d) / "*") for d in outside_dirs):
            required.append(self._build_outside_directory_permission(glob))

        return required

    def resolve_permission(self, args: BashArgs) -> PermissionContext | None:
        if is_windows():
            return None

        command_parts = _extract_commands(args.command)
        if not command_parts:
            return None

        guardrail_permission = self._resolve_guardrail_permission(command_parts)
        if (
            guardrail_permission
            and guardrail_permission.permission == ToolPermission.NEVER
        ):
            return guardrail_permission
        outside_dirs = _collect_outside_dirs(command_parts)
        if (
            self._is_unconditionally_allowed(command_parts, outside_dirs)
            and not guardrail_permission
        ):
            return PermissionContext(permission=ToolPermission.ALWAYS)

        required = self._build_required_permissions(command_parts, outside_dirs)
        if guardrail_permission:
            required.extend(guardrail_permission.required_permissions)
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
                await kill_async_subprocess(proc)
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
                await kill_async_subprocess(proc)
