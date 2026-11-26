from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
import logging
import os
import sys
from typing import cast

from pydantic import BaseModel, Field, JsonValue, ValidationError

from mistralai_vibe_local_harness._native import restored_bash_call
from mistralai_vibe_local_harness.protocol import (
    RustAction,
    RustBashTimeoutSettings,
    RustEvent,
    RustProtocolError,
    RustRuntimeBuiltinToolCallAction,
    RustToolFailedEvent,
    RustToolFailureResult,
    RustToolSucceededEvent,
    RustToolSuccessResult,
)
from mistralai_vibe_local_harness.vibe import _sandbox_helper
from mistralai_vibe_local_harness.vibe._runtime_config import LocalRuntimeAdapterConfig
from mistralai_vibe_local_harness.vibe._sandbox import (
    SandboxAdapter,
    SandboxToolError,
    SandboxToolTimeoutError,
    run_helper,
)
from mistralai_vibe_local_harness.vibe._windows_path import with_registry_path

SANDBOX_TIMEOUT_RESERVE_SECONDS = 30.0
"""Time the sandbox helper gets past the command's own timeout to report it."""
SANDBOX_LOGIN_ENV_TIMEOUT_SECONDS = _sandbox_helper.LOGIN_ENV_TIMEOUT_SECONDS + 10.0
"""Time the helper call that reads the login profile in a sandbox gets: the
profile's own limit, and time to start the helper."""
DEFAULT_TIMEOUT_SECONDS = 120
"""Seconds a bash command runs in a Vibe Session when the call does not say."""
MAX_TIMEOUT_SECONDS = 300
"""The longest timeout a bash call may ask for in a Vibe Session."""
BASH_TIMEOUT = RustBashTimeoutSettings(
    default_seconds=DEFAULT_TIMEOUT_SECONDS, max_seconds=MAX_TIMEOUT_SECONDS
)
"""The bash timeouts a Vibe Session runs unless the user sets their own; the
Harness Core states them to the model and fills the default into a call
without a timeout."""


def bash_timeout(
    default_seconds: int | None,
    max_seconds: int | None,
    *,
    default_setting: str,
    max_setting: str,
) -> RustBashTimeoutSettings:
    """The bash timeouts a Session runs: the default and maximum set in its Host's
    configuration, or ``BASH_TIMEOUT`` when neither is set.

    The default is what a call without a timeout gets; the maximum caps what a call
    may ask for. They are set together: one without the other is a configuration
    error naming both ``default_setting`` and ``max_setting``, the names they are set
    by. Neither is worked out from the other.

    The agents SDK runtime has a twin of this function; the bash timeout part of
    agents/harness/runtimes/python/ARCHITECTURE.md says why and what keeps them equal.
    """
    if default_seconds is None and max_seconds is None:
        return BASH_TIMEOUT
    if default_seconds is None or max_seconds is None:
        set_alone, missing = (
            (default_setting, max_setting)
            if max_seconds is None
            else (max_setting, default_setting)
        )
        raise ValueError(f"{set_alone} is set without {missing}. Set both, or neither.")
    return RustBashTimeoutSettings(
        default_seconds=default_seconds, max_seconds=max_seconds
    )


def held_to_bash_limits(
    execute: Callable[[RustAction], Awaitable[RustEvent]],
    bash_timeout: Callable[[], RustBashTimeoutSettings | None],
) -> Callable[[RustAction], Awaitable[RustEvent]]:
    """``execute`` with each bash call held to the timeouts ``bash_timeout`` gives now,
    as Core holds a fresh one: given the default when it sets no timeout, refused above
    the maximum. Every other Action goes to ``execute`` unchanged.

    Core fills and checks a call when the model makes it. A restored Session replays
    under the settings its journal was written with and adopts the current ones only
    once it is idle, so a bash call it saved under other timeouts, or none, is held to
    the current ones before it runs. Core holds it (``restored_bash_call``), as it
    does for the agents SDK runtime.
    """

    async def execute_held(action: RustAction) -> RustEvent:
        if not (
            isinstance(action, RustRuntimeBuiltinToolCallAction)
            and action.call.name == "file_system.bash"
        ):
            return await execute(action)
        limits = bash_timeout()
        filled, refusal = restored_bash_call(
            json.dumps(action.call.arguments),
            bash_timeout_json=None if limits is None else limits.model_dump_json(),
        )
        if refusal is not None:
            return RustToolFailedEvent(
                action_id=action.action_id,
                call_id=action.call_id,
                result=RustToolFailureResult.model_validate_json(refusal),
            )
        if filled is not None:
            call = action.call.model_copy(update={"arguments": json.loads(filled)})
            action = action.model_copy(update={"call": call})
        return await execute(action)

    return execute_held


logger = logging.getLogger(__name__)


class ShellSession:
    """What one Session's bash commands share.

    ``id`` names where a sandbox keeps what the shell's login profile set, so
    the profile runs once per Session; see ``_sandbox_helper.login_shell``.
    In a sandbox the profile is read by a helper call of its own, ahead of the
    first command and under its own time limit, so the profile's time does
    not count against a command's.
    """

    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self._login_env_lock = asyncio.Lock()
        # The sandbox the profile was read in, and whether commands there
        # read what it set: a Session can move into another sandbox.
        self._sandbox_login_env: tuple[SandboxAdapter, bool] | None = None

    async def sandbox_login_env(
        self, sandbox: SandboxAdapter, config: LocalRuntimeAdapterConfig
    ) -> bool:
        """Read the login profile in ``sandbox`` unless it was read there, and
        return whether commands should run with what it set.

        False once the profile failed or ran too long: commands then run with
        the sandbox's environment as it is.
        """
        async with self._login_env_lock:
            if self._sandbox_login_env is None or (
                self._sandbox_login_env[0] is not sandbox
            ):
                read = await _read_sandbox_login_env(sandbox, config, self.id)
                self._sandbox_login_env = (sandbox, read)
            return self._sandbox_login_env[1]


@dataclass(frozen=True, slots=True)
class SpawnedShell:
    process: asyncio.subprocess.Process
    output_encoding: str


class BashArgs(BaseModel):
    command: str = Field(min_length=1)
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)


class BashResult(BaseModel):
    command: str
    stdout: str
    stderr: str
    returncode: int
    was_truncated: bool
    timed_out: bool = False


async def execute_shell_tool(
    action: RustRuntimeBuiltinToolCallAction,
    config: LocalRuntimeAdapterConfig,
    *,
    session: ShellSession | None = None,
    timeout_advice: str | None = None,
) -> RustToolSucceededEvent | RustToolFailedEvent:
    """Run a bash tool call, in ``session`` when given; see ``ShellSession``.

    ``timeout_advice`` ends a timeout note; it is the Harness Core's
    ``process_tools_timeout_advice`` for the session, if any.
    """
    try:
        if action.call.name != "file_system.bash":
            raise ValueError(f"Unsupported shell tool: {action.call.name}")
        result = await run_bash(
            BashArgs.model_validate(action.call.arguments),
            config,
            session=session,
            timeout_advice=timeout_advice,
        )
        return _succeeded(action, result.model_dump(mode="json"))
    except asyncio.CancelledError:
        raise
    except (OSError, TimeoutError, ValidationError, ValueError) as exc:
        return _failed(action, str(exc))


async def run_bash(
    args: BashArgs,
    config: LocalRuntimeAdapterConfig,
    *,
    session: ShellSession | None = None,
    timeout_advice: str | None = None,
) -> BashResult:
    """Run a bash command where the workspace lives.

    A Sandbox Adapter runs it in the sandbox, with the sandbox's environment
    and the fixed non-interactive settings; neither the host's environment nor
    ``config.env`` is passed in. A command that runs past its timeout is
    stopped, and the result has what it printed until then; see
    ``_sandbox_helper.collect_bash``, also for ``timeout_advice``.
    """
    if config.sandbox is not None:
        return await _run_bash_in_sandbox(
            args, config, config.sandbox, session=session, timeout_advice=timeout_advice
        )
    spawned = await _spawn_bash_command(args.command, config)
    return BashResult.model_validate(
        await _sandbox_helper.collect_bash(
            spawned.process,
            command=args.command,
            timeout_seconds=args.timeout_seconds,
            output_encoding=spawned.output_encoding,
            timeout_advice=timeout_advice or "",
        )
    )


async def _run_bash_in_sandbox(
    args: BashArgs,
    config: LocalRuntimeAdapterConfig,
    sandbox: SandboxAdapter,
    *,
    session: ShellSession | None,
    timeout_advice: str | None,
) -> BashResult:
    # Without a Session, nothing keeps the profile between helper calls, so
    # the command's own call reads it.
    login_env = (
        True if session is None else await session.sandbox_login_env(sandbox, config)
    )
    try:
        response = await run_helper(
            sandbox,
            "bash",
            {
                "command": args.command,
                "timeout_seconds": args.timeout_seconds,
                "cwd": str(config.workspace.cwd),
                "shell": config.shell,
                "session_id": None if session is None else session.id,
                "login_env": login_env,
                "timeout_advice": timeout_advice or "",
            },
            timeout=args.timeout_seconds + SANDBOX_TIMEOUT_RESERVE_SECONDS,
            crashes=config.helper_crashes,
        )
    except SandboxToolTimeoutError as exc:
        # The sandbox stopped the helper, and the command with it: a timeout,
        # as a local one is, but the output went with the helper. The
        # sandbox's limit was shorter than the command's, or the helper's own
        # timeout would have stopped the command first, so it names its own.
        limit = (
            "at the sandbox's time limit"
            if exc.timeout is None
            else f"after {exc.timeout:g}s, the sandbox's time limit"
        )
        advice = f" {timeout_advice}" if timeout_advice else ""
        return BashResult(
            command=args.command,
            stdout="",
            stderr=(
                f"[The command timed out {limit} and was stopped."
                f" Its output was lost.{advice}]"
            ),
            returncode=-1,
            was_truncated=False,
            timed_out=True,
        )
    if isinstance(response, dict) and isinstance(
        warning := response.pop("login_env_warning", None), str
    ):
        logger.warning(warning)
    return BashResult.model_validate(response)


async def _read_sandbox_login_env(
    sandbox: SandboxAdapter, config: LocalRuntimeAdapterConfig, session_id: str
) -> bool:
    """Whether bash commands in ``sandbox`` should read the login profile,
    after reading it for them; see ``ShellSession.sandbox_login_env``.
    """
    try:
        response = await run_helper(
            sandbox,
            "login-env",
            {"shell": config.shell, "session_id": session_id},
            timeout=SANDBOX_LOGIN_ENV_TIMEOUT_SECONDS,
            crashes=config.helper_crashes,
        )
    except SandboxToolTimeoutError:
        logger.warning(
            "Reading the shell's login profile in the sandbox ran past %gs;"
            " commands run without what it sets.",
            SANDBOX_LOGIN_ENV_TIMEOUT_SECONDS,
        )
        return False
    except SandboxToolError as exc:
        # Such as a shell that is not there, or a helper that crashed: each
        # command's own call reads the profile then, and reports the shell.
        logger.warning(
            "Could not read the shell's login profile in the sandbox ahead of"
            " its commands; each command reads it instead: %s",
            exc,
        )
        return True
    if isinstance(response, dict) and response.get("captured") is True:
        return True
    shell = response.get("shell") if isinstance(response, dict) else None
    logger.warning(
        _sandbox_helper.login_env_failure_message(
            shell if isinstance(shell, str) else "the shell"
        )
    )
    return False


async def _spawn_bash_command(
    command: str, config: LocalRuntimeAdapterConfig
) -> SpawnedShell:
    env = _shell_env(config.env)
    argv = _shell_argv(command, config, env)
    if config.command_environment == "unix":
        # The login profile is read once, not by a login shell per command,
        # and before the command starts, outside its timeout. It runs with
        # this process's environment and ``config.env``, the base a sandbox
        # command gets from its sandbox; a bash call has no variables of its
        # own. The fixed non-interactive settings win over what it sets. The
        # command first sources the functions and aliases the profile defined.
        login = await asyncio.to_thread(
            _sandbox_helper.login_shell,
            argv[0],
            {**os.environ, **config.env},
            on_failure=logger.warning,
        )
        env = _sandbox_helper.shell_env(login.env, {}, windows=False)
        argv = [*argv[:-1], login.command(argv[-1])]
    process = await _sandbox_helper.spawn_shell(
        argv, cwd=str(config.workspace.cwd), env=env
    )
    return SpawnedShell(process=process, output_encoding="utf-8")


def _shell_argv(
    command: str, config: LocalRuntimeAdapterConfig, env: dict[str, str]
) -> list[str]:
    match config.command_environment:
        case "unix" if sys.platform != "win32":
            from mistralai_vibe_local_harness.vibe._processes._posix import (
                PosixTerminalBackend,
            )

            shell = PosixTerminalBackend().resolve_shell(config.shell, env)
            return [shell, "-c", command]
        case "git_bash" | "powershell" if sys.platform == "win32":
            from mistralai_vibe_local_harness.vibe._processes._windows import (
                WindowsTerminalBackend,
            )

            backend = WindowsTerminalBackend(config.command_environment)
            shell = backend.resolve_shell(config.shell, env)
            return backend.command_argv(shell, command, interactive=False)
        case _:
            raise ValueError(
                f"Unsupported command environment on {sys.platform}: {config.command_environment}"
            )


def _shell_env(extra_env: dict[str, str]) -> dict[str, str]:
    return with_registry_path(
        _sandbox_helper.shell_env(
            os.environ, extra_env, windows=sys.platform == "win32"
        )
    )


def _succeeded(
    action: RustRuntimeBuiltinToolCallAction, structured_content: dict[str, JsonValue]
) -> RustToolSucceededEvent:
    return RustToolSucceededEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolSuccessResult.model_validate({
            "structured_content": cast(JsonValue, structured_content)
        }),
    )


def _failed(
    action: RustRuntimeBuiltinToolCallAction, message: str
) -> RustToolFailedEvent:
    return RustToolFailedEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolFailureResult(
            error=RustProtocolError(
                code="tool_failed", message=message, retryable=False, details=None
            )
        ),
    )


__all__ = ["BASH_TIMEOUT", "bash_timeout", "execute_shell_tool"]
