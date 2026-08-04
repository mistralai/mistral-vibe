from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import os
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from vibe import VIBE_ROOT
from vibe.core.tools.base import BaseTool, BaseToolState, InvokeContext, ToolError
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
    ExperimentalBashResult,
    ExperimentalBashToolConfig,
    ManagedShellError,
    _BashPermissionMixin,
    posix_shell_default_allowlist,
    posix_shell_default_denylist,
    posix_shell_default_denylist_standalone,
)
from vibe.core.tools.builtins.managed_shell import backend as managed_shell_backend
from vibe.core.tools.builtins.managed_shell._windows import (
    build_windows_shell_argv,
    git_bash_shell_available,
    resolve_git_bash_shell,
)
from vibe.core.tools.builtins.managed_shell.backend import ManagedShellBackendError
from vibe.core.tools.io_port import ShellCommandRequest
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolResultEvent, ToolStreamEvent
from vibe.core.utils import is_windows, kill_async_subprocess
from vibe.utils.io import decode_safe
from vibe.utils.tool_presentation import ToolEffectKind

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


def _get_git_bash_env_overrides(overrides: dict[str, str] | None) -> dict[str, str]:
    env = {
        "CI": "true",
        "NONINTERACTIVE": "1",
        "NO_TTY": "1",
        "TERM": "dumb",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "LESS": "-FX",
    }
    if overrides:
        env.update(overrides)
    return env


def _get_git_bash_base_env(overrides: dict[str, str] | None) -> dict[str, str]:
    return {**os.environ, **_get_git_bash_env_overrides(overrides)}


def _completed_git_bash_result(
    *, command: str, shell: str, stdout: str, stderr: str, returncode: int
) -> ExperimentalBashResult:
    if returncode != 0:
        message = f"Command failed: {command!r}\nReturn code: {returncode}"
        if stderr:
            message += f"\nStderr: {stderr}"
        if stdout:
            message += f"\nStdout: {stdout}"
        raise ToolError(message)

    return ExperimentalBashResult(
        command=command,
        status="completed",
        exit_code=returncode,
        shell=shell,
        output=stdout + stderr,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
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
    cwd: str | None = Field(default=None, description="Working directory override.")
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
    BaseTool[GitBashArgs, ExperimentalBashResult, GitBashToolConfig, BaseToolState],
    ToolUIData[GitBashArgs, ExperimentalBashResult],
):
    effect_kind = ToolEffectKind.SHELL
    description: ClassVar[str] = "Run a Git Bash command."
    shell_rollout: ClassVar[str | None] = "managed"
    prompt_path = (
        VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "git_bash_fallback.md"
    )

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        _ = config
        return is_windows() and git_bash_shell_available()

    @classmethod
    def format_call_display(cls, args: GitBashArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"git_bash: {args.command}",
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
        return ToolResultDisplay(success=True, verb="Ran", message=event.result.command)

    @classmethod
    def get_status_text(cls) -> str:
        return "Running Git Bash command"

    def resolve_permission(self, args: GitBashArgs) -> PermissionContext | None:
        return self._resolve_posix_shell_permission(
            command=args.command,
            cwd=args.cwd,
            required_context_permissions=_build_git_bash_context_permissions(
                args.shell, args.env
            ),
        )

    async def run(
        self, args: GitBashArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ExperimentalBashResult, None]:
        requested_timeout = (
            float(args.timeout) if args.timeout is not None else args.timeout_seconds
        )
        timeout = self._resolve_timeout(requested_timeout)
        max_bytes = self.config.max_output_bytes
        proc: asyncio.subprocess.Process | None = None
        try:
            cwd = Path(args.cwd).expanduser().resolve() if args.cwd else self.cwd
            shell = resolve_git_bash_shell(args.shell, self.config.shell)
            argv = build_windows_shell_argv(shell, args.command)
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
                            command=argv[0],
                            args=argv[1:],
                            env=_get_git_bash_env_overrides(args.env),
                            cwd=cwd,
                            timeout=timeout,
                            max_output_bytes=max_bytes,
                        )
                    )
                except TimeoutError:
                    raise ToolError(
                        f"Command timed out after {timeout:g}s: {args.command!r}"
                    ) from None
                yield _completed_git_bash_result(
                    command=args.command,
                    shell=shell,
                    stdout=result.stdout[:max_bytes],
                    stderr=result.stderr[:max_bytes],
                    returncode=result.returncode,
                )
                return

            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(cwd),
                env=_get_git_bash_base_env(args.env),
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                await kill_async_subprocess(proc)
                raise ToolError(
                    f"Command timed out after {timeout:g}s: {args.command!r}"
                )

            stdout = _decode_limited(stdout_bytes, max_bytes)
            stderr = _decode_limited(stderr_bytes, max_bytes)
            returncode = proc.returncode or 0
            yield _completed_git_bash_result(
                command=args.command,
                shell=shell,
                stdout=stdout,
                stderr=stderr,
                returncode=returncode,
            )
        except (ToolError, asyncio.CancelledError):
            raise
        except (ManagedShellError, ManagedShellBackendError) as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"Error running command {args.command!r}: {exc}") from exc
        finally:
            if proc is not None:
                await kill_async_subprocess(proc)

    def _resolve_timeout(self, requested: float | None) -> float:
        timeout = self.config.default_timeout if requested is None else requested
        return min(timeout, self.config.max_timeout_seconds)


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

    @classmethod
    def format_call_display(cls, args: ExperimentalBashArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"git_bash: {args.command}",
            verb="Running",
            message=args.command,
            settled_verb="Ran",
            settled_message=args.command,
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Running Git Bash command"

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
    session_label: ClassVar[str] = "git_bash"

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
    session_label: ClassVar[str] = "git_bash"

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
    session_label: ClassVar[str] = "git_bash"

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
    session_label: ClassVar[str] = "git_bash"

    @classmethod
    def get_name(cls) -> str:
        return "git_bash_log_file"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _git_bash_managed_shell_enabled(config)


def _decode_limited(raw: bytes | None, max_bytes: int) -> str:
    if not raw:
        return ""
    return decode_safe(raw[:max_bytes], from_subprocess=True).text
