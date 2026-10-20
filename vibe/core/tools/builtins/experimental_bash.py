from __future__ import annotations

from collections.abc import Collection
import functools
import os
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, get_args

from pydantic import AliasChoices, BaseModel, Field, computed_field, model_validator

from vibe.core.scratchpad import is_scratchpad_path
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, ToolPermission
from vibe.core.tools.builtins._shell_command_policy import (
    analyze_shell_command_policy,
    git_repository_requires_approval,
    path_candidates,
)
from vibe.core.tools.builtins._shell_permission_analysis import analyze_shell_command
from vibe.core.tools.builtins.bash import (
    BashToolConfig,
    _expand_guardrail_commands,
    _git_repository_permission_pattern,
    _split_command_tokens,
    _update_guardrail_cwds,
    command_session_pattern,
    needs_exact_command_scope,
    scoped_command_parts,
)
from vibe.core.tools.builtins.managed_shell import backend as managed_shell_backend
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)
from vibe.core.tools.utils import (
    ToolPath,
    is_path_within_workdir,
    resolve_tool_path,
    shell_path_scope_root,
)
from vibe.core.utils import is_windows
from vibe.core.workspace import Workspace
from vibe.permissions import (
    PathGrantScope,
    path_grant_pattern,
    path_grant_pattern_matches,
)

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.config.harness_files import HarnessFilesManager

Status = Literal["running", "completed", "killed", "timed_out", "orphaned"]
LogAction = Literal["read", "write", "append"]
SessionAction = Literal["list", "inspect", "kill", "reset"]

DEFAULT_INLINE_BYTES = 30_000
DEFAULT_MAX_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_POLL_SECONDS = 300.0

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


def _looks_like_path(token: str) -> bool:
    return (
        token.startswith(os.sep)
        or token.startswith("~")
        or token.startswith(".")
        or "/" in token
        or "\\" in token
    )


def _collect_outside_paths(
    command_parts: list[str],
    *,
    command_cwd: Path,
    workspace: Workspace,
    scratchpad_dir: Path | None,
    path_commands: Collection[str] = _PATH_COMMANDS,
    case_sensitive_commands: bool = True,
) -> set[str]:
    paths: set[str] = set()
    if not is_path_within_workdir(
        str(command_cwd), workspace=workspace
    ) and not is_scratchpad_path(str(command_cwd), scratchpad_dir=scratchpad_dir):
        paths.add(str(command_cwd))

    for part in command_parts:
        tokens = _split_command_tokens(part)
        command = tokens[0] if tokens else None
        if not command:
            continue
        command_name = command if case_sensitive_commands else command.lower()
        for token in path_candidates(
            tokens, inspect_positional_paths=command_name in path_commands
        ):
            if not _looks_like_path(token):
                continue

            resolved = resolve_tool_path(token, command_cwd)

            if is_path_within_workdir(str(resolved), workspace=workspace):
                continue
            if is_scratchpad_path(str(resolved), scratchpad_dir=scratchpad_dir):
                continue

            paths.add(str(resolved))
    return paths


def _collect_outside_dirs(
    command_parts: list[str],
    *,
    command_cwd: Path,
    workspace: Workspace,
    scratchpad_dir: Path | None,
    path_commands: Collection[str] = _PATH_COMMANDS,
    case_sensitive_commands: bool = True,
) -> set[str]:
    """Compatibility helper returning parents for outside paths."""
    paths = _collect_outside_paths(
        command_parts,
        command_cwd=command_cwd,
        workspace=workspace,
        scratchpad_dir=scratchpad_dir,
        path_commands=path_commands,
        case_sensitive_commands=case_sensitive_commands,
    )
    return {
        str(Path(path) if Path(path).is_dir() else Path(path).parent) for path in paths
    }


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
    max_timeout_seconds: float | None = Field(
        default=None,
        description=(
            "Longest foreground wait one tool call may ask for, in seconds. Unset,"
            f" the native shell tools allow {DEFAULT_MAX_TIMEOUT_SECONDS:g}, or the"
            " default timeout when that is longer, and the unified harness its own"
            " maximum."
        ),
    )
    max_inline_bytes: int = Field(
        default=DEFAULT_INLINE_BYTES,
        validation_alias=AliasChoices("max_inline_bytes", "max_inline_chars"),
        description="Maximum output bytes read before inline decoding.",
    )
    shell: str | None = Field(
        default=None, description="Optional default shell executable override."
    )

    def resolve_timeout(self, requested: float | None) -> float:
        """The seconds a native shell tool waits for a call that asks for
        ``requested``, or for none: the default timeout, at most the maximum.
        """
        default = self.native_default_timeout
        maximum = (
            max(DEFAULT_MAX_TIMEOUT_SECONDS, default)
            if self.max_timeout_seconds is None
            else self.max_timeout_seconds
        )
        return min(default if requested is None else requested, maximum)


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
    # Command prefixes and typed path grants are what a shell reads back.
    allowlist_scopes: ClassVar[frozenset[PermissionScope]] = frozenset({
        PermissionScope.COMMAND_PATTERN,
        PermissionScope.OUTSIDE_DIRECTORY,
    })

    if TYPE_CHECKING:

        @property
        def config(self) -> ConfigT: ...

        cwd: Path
        harness_files: HarnessFilesManager
        workspace: Workspace
        scratchpad_dir: Path | None

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

    @staticmethod
    def _tokenize_command(command: str) -> list[str]:
        return _split_command_tokens(command)

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
            tokens = self._tokenize_command(part)
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
        self,
        command_parts: list[str],
        outside_paths: set[str],
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
            not outside_paths
        )

    def _build_required_permissions(
        self,
        command_parts: list[str],
        outside_paths: set[str],
        *,
        include_allowlisted: bool = False,
    ) -> list[RequiredPermission]:
        """What this call needs on account of the commands it runs.

        Context permissions are the caller's to append: they scope the shell and
        the environment a call was handed, not the command, and
        ``needs_exact_command_scope`` reads this list for whether the command
        itself came out scoped.
        """
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

            session_pattern, literal = command_session_pattern(tokens)
            if session_pattern in seen_session:
                continue
            seen_session.add(session_pattern)
            required.append(
                self._build_command_required_permission(
                    invocation_pattern=part,
                    session_pattern=session_pattern,
                    label=session_pattern,
                    literal=literal,
                )
            )

        for path in sorted(outside_paths):
            required.append(self._build_outside_directory_permission(path))

        return required

    def _resolve_posix_shell_permission(
        self,
        *,
        command: str,
        cwd: str | None,
        required_context_permissions: list[RequiredPermission] | None = None,
    ) -> PermissionContext | None:
        analysis = analyze_shell_command(command)
        command_parts = list(analysis.command_parts)
        if not command_parts and not analysis.requires_approval:
            return None

        command_cwd = resolve_tool_path(cwd, self.cwd)
        guardrail_permission = self._resolve_guardrail_permission(
            command_parts, command_cwd=command_cwd
        )
        if (
            guardrail_permission
            and guardrail_permission.permission == ToolPermission.NEVER
        ):
            return guardrail_permission

        outside_paths = _collect_outside_paths(
            command_parts,
            command_cwd=command_cwd,
            workspace=self.workspace,
            scratchpad_dir=self.scratchpad_dir,
        )
        outside_paths = {
            path
            for path in outside_paths
            if not any(
                path_grant_pattern_matches(path, pattern)
                for pattern in self.config.allowlist
            )
        }
        context_required = required_context_permissions or []
        if (
            self._is_unconditionally_allowed(
                command_parts, outside_paths, context_required
            )
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
                    invocation_pattern=command,
                    session_pattern=command,
                    label=analysis.approval_label,
                    literal=True,
                )
            )
        required.extend(context_required)
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
):
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


class BashOutput(
    _DescriptionOnlyPromptMixin,
    BaseTool[BashOutputArgs, BashOutputResult, BashOutputConfig, BaseToolState],
):
    description: ClassVar[str] = "Poll output from a running or completed bash session."
    shell_rollout: ClassVar[str | None] = "managed"
    local_managed_shell_only: ClassVar[bool] = True
    shell_family: ClassVar[str] = "posix"
    session_prefix: ClassVar[str] = "bash"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _experimental_bash_enabled(config)


class BashStdin(
    _DescriptionOnlyPromptMixin,
    BaseTool[BashStdinArgs, BashStdinResult, BashStdinConfig, BaseToolState],
):
    description: ClassVar[str] = "Send input to an interactive bash session."
    shell_rollout: ClassVar[str | None] = "managed"
    local_managed_shell_only: ClassVar[bool] = True
    shell_family: ClassVar[str] = "posix"
    session_prefix: ClassVar[str] = "bash"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _experimental_bash_enabled(config)


class BashSessions(
    _DescriptionOnlyPromptMixin,
    BaseTool[BashSessionsArgs, BashSessionsResult, BashSessionsConfig, BaseToolState],
):
    description: ClassVar[str] = "List, inspect, kill, or reset managed bash sessions."
    shell_rollout: ClassVar[str | None] = "managed"
    local_managed_shell_only: ClassVar[bool] = True
    shell_family: ClassVar[str] = "posix"
    session_prefix: ClassVar[str] = "bash"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _experimental_bash_enabled(config)


class BashLogFile(
    _DescriptionOnlyPromptMixin,
    BaseTool[BashLogFileArgs, BashLogFileResult, BashLogFileConfig, BaseToolState],
):
    description: ClassVar[str] = "Read or annotate managed bash output files."
    shell_rollout: ClassVar[str | None] = "managed"
    local_managed_shell_only: ClassVar[bool] = True
    shell_family: ClassVar[str] = "posix"
    session_prefix: ClassVar[str] = "bash"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _experimental_bash_enabled(config)

    def resolve_permission(self, args: BashLogFileArgs) -> PermissionContext | None:
        if args.action == "read":
            return PermissionContext(permission=ToolPermission.ALWAYS)
        return PermissionContext(permission=self.config.permission)
