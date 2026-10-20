from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from vibe import VIBE_ROOT
from vibe.core.tools.base import BaseTool, BaseToolState
from vibe.core.tools.builtins.bash import CapturedShellResult
from vibe.core.tools.builtins.experimental_bash import (
    BashLogFile,
    BashLogFileArgs,
    BashLogFileResult,
    BashOutput,
    BashOutputArgs,
    BashOutputResult,
    BashSessions,
    BashSessionsArgs,
    BashSessionsResult,
    BashStdin,
    BashStdinArgs,
    BashStdinResult,
    ExperimentalBash,
    ExperimentalBashArgs,
    ExperimentalBashToolConfig,
    _BashPermissionMixin,
    posix_shell_default_allowlist,
    posix_shell_default_denylist,
    posix_shell_default_denylist_standalone,
)
from vibe.core.tools.builtins.managed_shell import backend as managed_shell_backend
from vibe.core.tools.builtins.managed_shell._windows import git_bash_shell_available
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)
from vibe.core.tools.utils import ToolPath
from vibe.core.utils import is_windows

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema


GIT_BASH_SESSION_PREFIX = "git_bash"
# Load-bearing: keeps the inherited args/result models in this module's globals so
# get_type_hints() can resolve the companion tools' annotations under
# `from __future__ import annotations`. Do not remove — see get_parameters().
_INHERITED_ANNOTATION_MODELS = (
    BashLogFileArgs,
    BashLogFileResult,
    BashOutputArgs,
    BashOutputResult,
    BashSessionsArgs,
    BashSessionsResult,
    BashStdinArgs,
    BashStdinResult,
)


def _git_bash_managed_shell_enabled(config: VibeConfigSchema | None) -> bool:
    _ = config
    return bool(
        is_windows()
        and git_bash_shell_available()
        and managed_shell_backend.managed_shell_supported("git_bash")
    )


class GitBashToolConfig(ExperimentalBashToolConfig):
    allowlist: list[str] = Field(
        default_factory=posix_shell_default_allowlist,
        description="Command prefixes that are automatically allowed",
    )
    denylist: list[str] = Field(
        default_factory=posix_shell_default_denylist,
        description="Command prefixes that are automatically denied",
    )
    denylist_standalone: list[str] = Field(
        default_factory=posix_shell_default_denylist_standalone,
        description="Commands that are denied only when run without arguments",
    )


class GitBashArgs(BaseModel):
    command: str = Field(description="Git Bash command to run.")
    timeout: int | None = Field(
        default=None, description="Backward-compatible timeout override in seconds."
    )
    timeout_seconds: float | None = Field(
        default=None,
        ge=0,
        description="Foreground wait time before the command is killed.",
    )
    cwd: ToolPath | None = Field(
        default=None, description="Working directory override."
    )
    env: dict[str, str] | None = Field(
        default=None, description="Environment variable overrides."
    )
    shell: str | None = Field(default=None, description="Shell executable override.")


def _build_command_required_permission(
    invocation_pattern: str, session_pattern: str, label: str
) -> RequiredPermission:
    return RequiredPermission(
        scope=PermissionScope.COMMAND_PATTERN,
        invocation_pattern=invocation_pattern,
        session_pattern=session_pattern,
        label=label,
    )


def _build_git_bash_context_permissions(
    shell: str | None, env: dict[str, str] | None
) -> list[RequiredPermission]:
    required: list[RequiredPermission] = []
    if shell:
        required.append(
            _build_command_required_permission(
                invocation_pattern=f"shell override: {shell}",
                session_pattern=f"shell override: {shell}",
                label=f"custom shell ({shell})",
            )
        )
    if env:
        names = ", ".join(sorted(env))
        required.append(
            _build_command_required_permission(
                invocation_pattern=f"env override: {names}",
                session_pattern="env override *",
                label=f"custom environment ({names})",
            )
        )
    return required


class GitBash(
    _BashPermissionMixin[GitBashToolConfig],
    BaseTool[GitBashArgs, CapturedShellResult, GitBashToolConfig, BaseToolState],
):
    description: ClassVar[str] = "Run a Git Bash command."
    shell_rollout: ClassVar[str | None] = "managed"
    prompt_path = (
        VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "git_bash_fallback.md"
    )

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        _ = config
        return is_windows() and git_bash_shell_available()

    def resolve_permission(self, args: GitBashArgs) -> PermissionContext | None:
        return self._resolve_posix_shell_permission(
            command=args.command,
            cwd=args.cwd,
            required_context_permissions=_build_git_bash_context_permissions(
                args.shell, args.env
            ),
        )


class ExperimentalGitBash(ExperimentalBash):
    description: ClassVar[str] = "Run a Git Bash command in a managed PTY session."
    prompt_path = (
        VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "managed_git_bash.md"
    )
    shell_rollout: ClassVar[str | None] = "managed"
    shell_family: ClassVar[str] = "git_bash"
    session_prefix: ClassVar[str] = GIT_BASH_SESSION_PREFIX

    @classmethod
    def get_name(cls) -> str:
        return "git_bash"

    @classmethod
    def _get_tool_config_class(cls) -> type[GitBashToolConfig]:
        return GitBashToolConfig

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _git_bash_managed_shell_enabled(config)

    def resolve_permission(
        self, args: ExperimentalBashArgs
    ) -> PermissionContext | None:
        return self._resolve_posix_shell_permission(
            command=args.command,
            cwd=args.cwd,
            required_context_permissions=_build_git_bash_context_permissions(
                args.shell, args.env
            ),
        )


class GitBashOutput(BashOutput):
    description: ClassVar[str] = (
        "Poll output from a running or completed Git Bash session."
    )
    shell_rollout: ClassVar[str | None] = "managed"
    shell_family: ClassVar[str] = "git_bash"
    session_prefix: ClassVar[str] = GIT_BASH_SESSION_PREFIX

    @classmethod
    def get_name(cls) -> str:
        return "git_bash_output"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _git_bash_managed_shell_enabled(config)


class GitBashStdin(BashStdin):
    description: ClassVar[str] = "Send input to an interactive Git Bash session."
    shell_rollout: ClassVar[str | None] = "managed"
    shell_family: ClassVar[str] = "git_bash"
    session_prefix: ClassVar[str] = GIT_BASH_SESSION_PREFIX

    @classmethod
    def get_name(cls) -> str:
        return "git_bash_stdin"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _git_bash_managed_shell_enabled(config)


class GitBashSessions(BashSessions):
    description: ClassVar[str] = (
        "List, inspect, kill, or reset managed Git Bash sessions."
    )
    shell_rollout: ClassVar[str | None] = "managed"
    shell_family: ClassVar[str] = "git_bash"
    session_prefix: ClassVar[str] = GIT_BASH_SESSION_PREFIX

    @classmethod
    def get_name(cls) -> str:
        return "git_bash_sessions"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _git_bash_managed_shell_enabled(config)


class GitBashLogFile(BashLogFile):
    description: ClassVar[str] = "Read or annotate managed Git Bash output files."
    shell_rollout: ClassVar[str | None] = "managed"
    shell_family: ClassVar[str] = "git_bash"
    session_prefix: ClassVar[str] = GIT_BASH_SESSION_PREFIX

    @classmethod
    def get_name(cls) -> str:
        return "git_bash_log_file"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _git_bash_managed_shell_enabled(config)
