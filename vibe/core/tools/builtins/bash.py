from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
import shlex
from typing import ClassVar, final

from pydantic import BaseModel, Field, computed_field

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
from vibe.core.tools.builtins._shell_command_policy import (
    analyze_shell_command_policy,
    git_repository_identity,
    git_repository_requires_approval,
    has_option_guardrails,
    path_candidates,
)
from vibe.core.tools.builtins._shell_permission_analysis import (
    ShellPermissionAnalysis,
    analyze_shell_command,
)
from vibe.core.tools.io_port import ShellCommandRequest
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import (
    ambient_workspace,
    is_path_within_workdir,
    resolve_tool_path,
    shell_path_scope_root,
)
from vibe.core.types import ToolResultEvent, ToolStreamEvent
from vibe.core.utils import is_windows, kill_async_subprocess
from vibe.core.utils.shell import spawn_shell_command, uses_posix_shell
from vibe.core.workspace import Workspace
from vibe.permissions import (
    PathGrantScope,
    path_grant_pattern,
    path_grant_pattern_matches,
)
from vibe.utils.io import decode_console_safe
from vibe.utils.paths import normalize_windows_path
from vibe.utils.tool_presentation import ToolEffectKind


def _extract_commands(command: str) -> list[str]:
    return list(analyze_shell_command(command).command_parts)


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


def _wrapped_guardrail_commands(command: str) -> list[str]:
    """Extract statically visible commands invoked by shell builtins."""
    tokens = _split_command_tokens(command)
    if not tokens:
        return []

    if tokens[0] == "eval":
        evaluated = " ".join(tokens[1:])
        if not evaluated:
            return []
        return list(analyze_shell_command(evaluated).command_parts)

    if tokens[0] != "exec":
        return []

    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-a":
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    if index >= len(tokens):
        return []
    return [" ".join(tokens[index:])]


def _expand_guardrail_commands(command_parts: list[str]) -> list[str]:
    """Expand shell wrappers without dropping repeated command occurrences.

    Repository guardrails depend on the directory reached at each occurrence,
    so equal command text cannot be deduplicated globally. The ancestry set only
    prevents a pathological wrapper expansion from cycling within one branch.
    """
    expanded: list[str] = []
    pending = [(part, frozenset()) for part in command_parts]
    while pending:
        part, ancestors = pending.pop(0)
        expanded.append(part)
        if part in ancestors:
            continue
        next_ancestors = ancestors | {part}
        pending.extend(
            (wrapped, next_ancestors) for wrapped in _wrapped_guardrail_commands(part)
        )
    return expanded


_WORKING_DIRECTORY_COMMANDS = {
    "cd",
    "chdir",
    "pushd",
    "push-location",
    "set-location",
    "sl",
}
_WORKING_DIRECTORY_POP_COMMANDS = {"popd", "pop-location"}
_WORKING_DIRECTORY_TOKEN_COUNT = 2
_SHELL_GLOB_CHARACTERS = frozenset("*?[")


def _update_guardrail_cwds(tokens: list[str], possible_cwds: set[Path]) -> bool:
    """Track every statically possible cwd; return whether it became unknown."""
    if not tokens:
        return False
    command = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if command in _WORKING_DIRECTORY_POP_COMMANDS:
        # The cwd set is monotonic, so a plain pop can only return to a location
        # already recorded by an earlier push. Named/option-bearing stacks are
        # not statically knowable and therefore fail closed.
        return len(tokens) != 1
    if command not in _WORKING_DIRECTORY_COMMANDS:
        return False
    if command in {"pushd", "push-location"} and len(tokens) == 1:
        # Swapping an existing directory stack cannot introduce a new path.
        return False
    if (
        len(tokens) != _WORKING_DIRECTORY_TOKEN_COUNT
        or tokens[1].startswith("-")
        or any(character in tokens[1] for character in _SHELL_GLOB_CHARACTERS)
    ):
        return True
    possible_cwds.update(
        resolve_tool_path(tokens[1], cwd) for cwd in tuple(possible_cwds)
    )
    return False


def _git_repository_permission_pattern(
    command: str, possible_cwds: set[Path], *, cwd_is_unknown: bool
) -> str:
    """Key a Git-reader approval to every repository it may inspect."""
    identities: set[str] = set()
    for cwd in possible_cwds:
        identity = git_repository_identity(cwd)
        if identity is None:
            try:
                identity = f"directory:{cwd.resolve()}"
            except OSError:
                identity = f"directory:{cwd.absolute()}"
        identities.add(identity)
    if cwd_is_unknown:
        identities.add("dynamic-directory")
    return f"{command} [git repositories: {' | '.join(sorted(identities))}]"


def _collect_outside_paths(
    command_parts: list[str],
    *,
    workspace: Workspace | None = None,
    scratchpad_dir: Path | None = None,
) -> set[str]:
    """Collect canonical paths referenced outside the workdir.

    Iterates file-manipulating commands (see _PATH_COMMANDS) and inspects
    their arguments as candidate paths. Skips flags (-r, --recursive) and
    chmod mode strings (+x).

    Only invoked under POSIX-shell semantics (see resolve_permission), where "/"
    is a valid path separator — including Git Bash on Windows, whose paths can
    look like /c/Users/... even though os.sep is "\\" there. Git Bash also
    accepts backslash-separated Windows paths.
    """
    workspace = workspace or ambient_workspace()
    resolved_cwd = workspace.cwd

    def is_within_workdir(path: str) -> bool:
        return is_path_within_workdir(path, workspace=workspace)

    paths: set[str] = set()
    for part in command_parts:
        tokens = _split_command_tokens(part)
        command = tokens[0] if tokens else None
        if not command:
            continue
        for token in path_candidates(
            tokens, inspect_positional_paths=command in _PATH_COMMANDS
        ):
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
            paths.add(str(resolved))
    return paths


def _collect_outside_dirs(
    command_parts: list[str],
    *,
    workspace: Workspace | None = None,
    scratchpad_dir: Path | None = None,
) -> set[str]:
    """Compatibility helper returning parents for outside paths."""
    paths = _collect_outside_paths(
        command_parts, workspace=workspace, scratchpad_dir=scratchpad_dir
    )
    return {
        str(Path(path) if Path(path).is_dir() else Path(path).parent) for path in paths
    }


def _matches_pattern(command: str, pattern: str) -> bool:
    return command == pattern or command.startswith(pattern + " ")


def command_session_pattern(tokens: list[str]) -> tuple[str, bool]:
    """The pattern a grant on this command is recorded under, and whether it is literal.

    ``build_session_pattern`` stars everything past the command's name, which is
    where guardrailed options sit: ``git log *``, earned by an innocuous
    ``git log $REF``, would cover ``git log --ext-diff`` and the guardrail would
    never be asked. A guardrailed command therefore keeps its own text -- read as
    text, since that text can itself hold the glob characters it is escaping.
    """
    if has_option_guardrails(tokens):
        return " ".join(tokens), True
    return build_session_pattern(tokens), False


def scoped_command_parts(
    analysis: ShellPermissionAnalysis, command_parts: list[str]
) -> tuple[list[str], bool]:
    """The parts a grant may be recorded against, and whether to include allowlisted.

    Once the extract stops describing what runs, no part is offered: emitting
    ``git *`` for ``git $SUB`` beside the exact command hands the widening over
    anyway.

    Where it does describe it, allowlisted parts contribute too. The approval
    owed for unreadable syntax has to sit on the commands that carried it rather
    than ride on whatever else the call needed -- an outside-workdir glob, a
    shell override -- and the allowlist already grants those commands every
    argument it can read.
    """
    if analysis.invalidates_scope:
        return [], False
    return command_parts, analysis.requires_approval


def needs_exact_command_scope(
    analysis: ShellPermissionAnalysis, required: list[RequiredPermission]
) -> bool:
    """Whether the command as written is the only scope left to record.

    Never added beside a pattern that would have generalised: the exact text
    releases this call and no other, so the user would answer for the session
    and still be asked next time.

    ``required`` must hold only what the commands earned. A context permission
    is ``COMMAND_PATTERN``-scoped too, and counting one would read ``[[ -n $FOO
    ]]`` under a shell override as a command that came out scoped when nothing
    about it was recorded at all.
    """
    if analysis.invalidates_scope:
        return True
    return analysis.requires_approval and not any(
        rp.scope is PermissionScope.COMMAND_PATTERN for rp in required
    )


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
    # Command prefixes and typed path grants are what a shell reads back.
    allowlist_scopes: ClassVar[frozenset[PermissionScope]] = frozenset({
        PermissionScope.COMMAND_PATTERN,
        PermissionScope.OUTSIDE_DIRECTORY,
    })

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
    def _build_command_required_permission(
        invocation_pattern: str,
        session_pattern: str,
        label: str,
        *,
        literal: bool = False,
    ) -> RequiredPermission:
        return RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern=invocation_pattern,
            session_pattern=session_pattern,
            label=label,
            literal=literal,
        )

    @staticmethod
    def _build_outside_directory_permission(path: str) -> RequiredPermission:
        return RequiredPermission(
            scope=PermissionScope.OUTSIDE_DIRECTORY,
            invocation_pattern=path,
            session_pattern=path_grant_pattern(path, PathGrantScope.EXACT),
            label=f"outside workdir ({path})",
            path_scope_root=shell_path_scope_root(path),
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
        self, command_parts: list[str], *, command_cwd: Path
    ) -> PermissionContext | None:
        option_required_by_command: dict[str, RequiredPermission] = {}
        possible_cwds = {command_cwd}
        cwd_is_unknown = False

        for part in _expand_guardrail_commands(command_parts):
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
            tokens = _split_command_tokens(part)
            cwd_is_unknown = (
                _update_guardrail_cwds(tokens, possible_cwds) or cwd_is_unknown
            )
            policy = analyze_shell_command_policy(tokens)
            repository_requires_approval = policy.inspect_git_repository and (
                cwd_is_unknown
                or any(
                    git_repository_requires_approval(tokens, cwd=cwd)
                    for cwd in possible_cwds
                )
            )
            if not (policy.requires_approval or repository_requires_approval):
                continue
            permission_pattern = part
            if policy.inspect_git_repository:
                permission_pattern = _git_repository_permission_pattern(
                    part, possible_cwds, cwd_is_unknown=cwd_is_unknown
                )
            option_required_by_command[part] = self._build_command_required_permission(
                invocation_pattern=permission_pattern,
                session_pattern=permission_pattern,
                label=part,
                literal=True,
            )

        if not option_required_by_command:
            return None
        return PermissionContext(
            permission=ToolPermission.ASK,
            required_permissions=list(option_required_by_command.values()),
        )

    def _is_unconditionally_allowed(
        self, command_parts: list[str], outside_paths: set[str]
    ) -> bool:
        if any(self._is_sensitive(part) for part in command_parts):
            return False

        if self.config.permission == ToolPermission.ALWAYS:
            return True

        return all(self._is_allowlisted(part) for part in command_parts) and (
            not outside_paths
        )

    def _build_required_permissions(
        self,
        command_parts: list[str],
        outside_paths: set[str],
        *,
        include_allowlisted: bool = False,
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
            if (
                not is_sensitive
                and not include_allowlisted
                and self._is_allowlisted(part)
            ):
                continue

            if is_sensitive:
                required.append(
                    self._build_command_required_permission(
                        invocation_pattern=part,
                        session_pattern=part,
                        label=part,
                        literal=True,
                    )
                )
                continue

            session_pat, literal = command_session_pattern(tokens)
            if session_pat in seen_session:
                continue
            seen_session.add(session_pat)
            required.append(
                self._build_command_required_permission(
                    invocation_pattern=part,
                    session_pattern=session_pat,
                    label=session_pat,
                    literal=literal,
                )
            )

        for path in sorted(outside_paths):
            required.append(self._build_outside_directory_permission(path))

        return required

    def resolve_permission(self, args: BashArgs) -> PermissionContext | None:
        if not uses_posix_shell():
            return None

        analysis = analyze_shell_command(args.command)
        command_parts = list(analysis.command_parts)
        if not command_parts and not analysis.requires_approval:
            return None

        guardrail_permission = self._resolve_guardrail_permission(
            command_parts, command_cwd=self.workspace.cwd
        )
        if (
            guardrail_permission
            and guardrail_permission.permission == ToolPermission.NEVER
        ):
            return guardrail_permission
        outside_paths = _collect_outside_paths(
            command_parts, workspace=self.workspace, scratchpad_dir=self.scratchpad_dir
        )
        outside_paths = {
            path
            for path in outside_paths
            if not any(
                path_grant_pattern_matches(path, pattern)
                for pattern in self.config.allowlist
            )
        }
        if (
            self._is_unconditionally_allowed(command_parts, outside_paths)
            and not guardrail_permission
            and not analysis.requires_approval
        ):
            return PermissionContext(permission=ToolPermission.ALWAYS)

        scoped_parts, include_allowlisted = scoped_command_parts(
            analysis, command_parts
        )
        required = self._build_required_permissions(
            scoped_parts, outside_paths, include_allowlisted=include_allowlisted
        )
        if guardrail_permission:
            required.extend(guardrail_permission.required_permissions)
        if needs_exact_command_scope(analysis, required):
            required.append(
                self._build_command_required_permission(
                    invocation_pattern=args.command,
                    session_pattern=args.command,
                    label=analysis.approval_label,
                    literal=True,
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
