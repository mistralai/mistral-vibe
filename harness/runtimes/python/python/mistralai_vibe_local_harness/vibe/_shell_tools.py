from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import sys
from typing import cast

from pydantic import BaseModel, Field, JsonValue, ValidationError

from mistralai_vibe_local_harness.protocol import (
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
    SandboxToolTimeoutError,
    run_helper,
)

MAX_OUTPUT_BYTES = _sandbox_helper.MAX_OUTPUT_BYTES
SANDBOX_TIMEOUT_RESERVE_SECONDS = 30.0
"""Time the sandbox helper gets past the command's own timeout to report it."""


@dataclass(frozen=True, slots=True)
class SpawnedShell:
    process: asyncio.subprocess.Process
    output_encoding: str


class BashArgs(BaseModel):
    command: str = Field(min_length=1)
    timeout_seconds: int = Field(default=300, gt=0)


class BashResult(BaseModel):
    command: str
    stdout: str
    stderr: str
    returncode: int
    was_truncated: bool


async def execute_shell_tool(
    action: RustRuntimeBuiltinToolCallAction, config: LocalRuntimeAdapterConfig
) -> RustToolSucceededEvent | RustToolFailedEvent:
    try:
        if action.call.name != "file_system.bash":
            raise ValueError(f"Unsupported shell tool: {action.call.name}")
        result = await run_bash(BashArgs.model_validate(action.call.arguments), config)
        return _succeeded(action, result.model_dump(mode="json"))
    except asyncio.CancelledError:
        raise
    except (OSError, TimeoutError, ValidationError, ValueError) as exc:
        return _failed(action, str(exc))


async def run_bash(args: BashArgs, config: LocalRuntimeAdapterConfig) -> BashResult:
    """Run a bash command where the workspace lives.

    A Sandbox Adapter runs it in the sandbox, with the sandbox's environment
    and the fixed non-interactive settings; neither the host's environment nor
    ``config.env`` is passed in.
    """
    if config.sandbox is not None:
        return await _run_bash_in_sandbox(args, config, config.sandbox)
    spawned = await _spawn_bash_command(args.command, config)
    return BashResult.model_validate(
        await _sandbox_helper.collect_bash(
            spawned.process,
            command=args.command,
            timeout_seconds=args.timeout_seconds,
            output_encoding=spawned.output_encoding,
        )
    )


async def _run_bash_in_sandbox(
    args: BashArgs, config: LocalRuntimeAdapterConfig, sandbox: SandboxAdapter
) -> BashResult:
    try:
        response = await run_helper(
            sandbox,
            "bash",
            {
                "command": args.command,
                "timeout_seconds": args.timeout_seconds,
                "cwd": str(config.workspace.cwd),
                "shell": config.shell,
            },
            timeout=args.timeout_seconds + SANDBOX_TIMEOUT_RESERVE_SECONDS,
            crashes=config.helper_crashes,
        )
    except SandboxToolTimeoutError as exc:
        # The sandbox stopped the command: the same failure as a local timeout.
        # Its limit was shorter than the command's, or the helper's own
        # timeout would have stopped the command first, so it names its own.
        limit = (
            "at the sandbox's time limit"
            if exc.timeout is None
            else f"after {exc.timeout:g}s"
        )
        raise TimeoutError(f"Command timed out {limit}: {args.command!r}") from exc
    return BashResult.model_validate(response)


async def _spawn_bash_command(
    command: str, config: LocalRuntimeAdapterConfig
) -> SpawnedShell:
    env = _shell_env(config.env)
    argv = _shell_argv(command, config, env)
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
            return [shell, "-lc", command]
        case "git_bash" | "powershell" if sys.platform == "win32":
            from mistralai_vibe_local_harness.vibe._processes._windows import (
                WindowsTerminalBackend,
            )

            shell = WindowsTerminalBackend(config.command_environment).resolve_shell(
                config.shell, env
            )
            flags = (
                ["-c"]
                if config.command_environment == "git_bash"
                else ["-NoLogo", "-NoProfile", "-Command"]
            )
            return [shell, *flags, command]
        case _:
            raise ValueError(
                f"Unsupported command environment on {sys.platform}: {config.command_environment}"
            )


def _shell_env(extra_env: dict[str, str]) -> dict[str, str]:
    return _sandbox_helper.shell_env(
        os.environ, extra_env, windows=sys.platform == "win32"
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


__all__ = ["execute_shell_tool"]
