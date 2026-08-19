from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from functools import lru_cache
from pathlib import Path
import shlex
from typing import ClassVar, final

from pydantic import BaseModel, Field, computed_field
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
from vibe.core.tools.io_port import ShellCommandRequest
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import is_path_within_workdir, resolve_tool_path
from vibe.core.types import ToolResultEvent, ToolStreamEvent
from vibe.core.utils import is_windows, kill_async_subprocess
from vibe.core.utils.shell import spawn_shell_command, uses_posix_shell
from vibe.utils.io import decode_console_safe
from vibe.utils.paths import normalize_windows_path
from vibe.utils.tool_presentation import ToolEffectKind


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
        _READ_ONLY_COMMANDS_POSIX if uses_posix_shell() else _READ_ONLY_COMMANDS_WINDOWS
    )


def _get_default_allowlist() -> list[str]:
    common = ["cd", "echo", "git diff", "git log", "git status", "tree", "whoami"]
    return common + default_read_only_commands()


def _get_default_denylist() -> list[str]:
    common = ["gdb", "pdb", "passwd"]

    if not uses_posix_shell():
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

    if not uses_posix_shell():
        return common + ["cmd", "powershell", "pwsh", "notepad"]

    return common + ["bash", "sh", "nohup", "vi", "vim", "emacs", "nano", "su"]


_MUTATING_PATH_COMMANDS = {"cd", "chmod", "chown", "cp", "mkdir", "mv", "rm", "touch"}

# Every command whose path arguments must be checked against the workdir
# boundary. This must stay a superset of the read-only allowlist: any command
# that can be auto-allowed (see _is_unconditionally_allowed) has to have its
# paths inspected first, otherwise `grep root /etc/passwd`, `od -c ~/.ssh/id_rsa`
# and friends would read outside the workdir without ever requiring the
# OUTSIDE_DIRECTORY permission.
_PATH_COMMANDS = _MUTATING_PATH_COMMANDS | set(_READ_ONLY_COMMANDS_POSIX)

_FIND_EXECUTION_PREDICATES = {"-exec", "-execdir", "-ok", "-okdir"}


def _split_command_tokens(command: str) -> list[str]:
    try:
        if not is_windows():
            return shlex.split(command)
        # On Windows, escape="" keeps backslashes literal so paths like
        # C:\Users\... survive tokenization; POSIX shlex would otherwise consume
        # them as escape characters. This must stay Windows-only: on POSIX the
        # backslash is a real escape and dropping it would corrupt path tokens.
        lexer = shlex.shlex(command, posix=True)
        lexer.whitespace_split = True
        lexer.escape = ""
        return list(lexer)
    except ValueError:
        return command.split()


def _collect_outside_dirs(
    command_parts: list[str],
    *,
    cwd: Path | None = None,
    project_roots: list[Path] | None = None,
    scratchpad_dir: Path | None = None,
) -> set[str]:
    """Collect parent directories referenced outside the workdir.

    Iterates file-manipulating commands (see _PATH_COMMANDS) and inspects
    their arguments as candidate paths. Skips flags (-r, --recursive) and
    chmod mode strings (+x). For any argument that resolves outside the current
    working directory, adds the parent directory (or the path itself when it is
    a directory) to the result set — suitable for building an OUTSIDE_DIRECTORY
    RequiredPermission.

    Only invoked under POSIX-shell semantics (see resolve_permission), where "/"
    is a valid path separator — including Git Bash on Windows, whose paths can
    look like /c/Users/... even though os.sep is "\\" there. Git Bash also
    accepts backslash-separated Windows paths.
    """
    resolved_cwd = (cwd or Path.cwd()).resolve()

    def is_within_workdir(path: str) -> bool:
        if project_roots is None:
            return is_path_within_workdir(path)
        return is_path_within_workdir(
            path, cwd=resolved_cwd, project_roots=project_roots
        )

    dirs: set[str] = set()
    for part in command_parts:
        tokens = _split_command_tokens(part)
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
                token.startswith("/")
                or token.startswith("~")
                or token.startswith(".")
                or "/" in token
                or "\\" in token
            ):
                continue
            path_token = normalize_windows_path(token)
            if is_within_workdir(path_token):
                continue
            if is_scratchpad_path(path_token, scratchpad_dir=scratchpad_dir):
                continue
            resolved = resolve_tool_path(path_token, resolved_cwd)
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
    command: str = Field(description="The shell command to execute")
    timeout: int | None = Field(
        default=None, description="Override the default command timeout."
    )


class CapturedShellResult(BaseModel):
    """Result of a shell that captures stdout and stderr as two separate pipes."""

    command: str
    shell: str = ""
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""

    # `model_dump` of a tool result is the `post_tool` hook payload, so dropping
    # this key outright would break hooks that read `tool_output.returncode`.
    @computed_field(description="Deprecated alias for `exit_code`.")
    @property
    def returncode(self) -> int:
        return self.exit_code


def completed_shell_result(
    *, command: str, stdout: str, stderr: str, exit_code: int, shell: str = ""
) -> CapturedShellResult:
    if exit_code != 0:
        message = f"Command failed: {command!r}\nReturn code: {exit_code}"
        if stderr:
            message += f"\nStderr: {stderr}"
        if stdout:
            message += f"\nStdout: {stdout}"
        raise ToolError(message)

    return CapturedShellResult(
        command=command, shell=shell, exit_code=exit_code, stdout=stdout, stderr=stderr
    )


class Bash(
    BaseTool[BashArgs, CapturedShellResult, BashToolConfig, BaseToolState],
    ToolUIData[BashArgs, CapturedShellResult],
):
    effect_kind = ToolEffectKind.SHELL
    shell_rollout: ClassVar[str | None] = "legacy"

    @classmethod
    def format_call_display(cls, args: BashArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"bash: {args.command}",
            verb="Running",
            message=args.command,
            settled_verb="Ran",
            settled_message=args.command,
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, CapturedShellResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        return ToolResultDisplay(success=True, verb="Ran", message=event.result.command)

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
            command_name = Path(base_command).name
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
        if not uses_posix_shell():
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
        outside_dirs = _collect_outside_dirs(
            command_parts,
            cwd=self.cwd,
            project_roots=self.harness_files.project_roots,
            scratchpad_dir=self.scratchpad_dir,
        )
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

    async def run(
        self, args: BashArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | CapturedShellResult, None]:
        timeout = args.timeout or self.config.default_timeout
        max_bytes = self.config.max_output_bytes

        if (
            ctx is not None
            and ctx.tool_io is not None
            and ctx.tool_io.supports_terminal
            and ctx.session_id is not None
        ):
            try:
                result = await ctx.tool_io.run_shell(
                    ShellCommandRequest(
                        session_id=ctx.session_id,
                        tool_call_id=ctx.tool_call_id,
                        command=args.command,
                        cwd=self.cwd,
                        timeout=timeout,
                        max_output_bytes=max_bytes,
                    )
                )
            except TimeoutError:
                raise self._build_timeout_error(args.command, timeout) from None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise ToolError(
                    f"Error running command {args.command!r}: {exc}"
                ) from exc
            yield completed_shell_result(
                command=args.command,
                stdout=result.stdout[:max_bytes],
                stderr=result.stderr[:max_bytes],
                exit_code=result.returncode,
            )
            return

        proc = None
        try:
            proc = await spawn_shell_command(args.command, cwd=self.cwd)

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                await kill_async_subprocess(proc)
                raise self._build_timeout_error(args.command, timeout)

            stdout = (
                decode_console_safe(stdout_bytes)[:max_bytes] if stdout_bytes else ""
            )
            stderr = (
                decode_console_safe(stderr_bytes)[:max_bytes] if stderr_bytes else ""
            )

            yield completed_shell_result(
                command=args.command,
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode or 0,
            )

        except (ToolError, asyncio.CancelledError):
            raise
        except Exception as exc:
            raise ToolError(f"Error running command {args.command!r}: {exc}") from exc
        finally:
            if proc is not None:
                await kill_async_subprocess(proc)
