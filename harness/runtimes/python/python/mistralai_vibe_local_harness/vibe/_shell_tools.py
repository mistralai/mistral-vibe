from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import os
import signal
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
from mistralai_vibe_local_harness.vibe._runtime_config import LocalRuntimeAdapterConfig

MAX_OUTPUT_BYTES = 16_000


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
    process: asyncio.subprocess.Process | None = None
    try:
        spawned = await _spawn_bash_command(args.command, config)
        process = spawned.process
        try:
            (
                stdout_bytes,
                stdout_truncated,
                stderr_bytes,
                stderr_truncated,
            ) = await asyncio.wait_for(
                _communicate_bounded(process), timeout=args.timeout_seconds
            )
        except TimeoutError as exc:
            await _kill(process)
            raise TimeoutError(
                f"Command timed out after {args.timeout_seconds}s: {args.command!r}"
            ) from exc

        stdout = _decode_output(stdout_bytes, spawned.output_encoding)
        stderr = _decode_output(stderr_bytes, spawned.output_encoding)
        return BashResult(
            command=args.command,
            stdout=stdout,
            stderr=stderr,
            returncode=process.returncode or 0,
            was_truncated=stdout_truncated or stderr_truncated,
        )
    except asyncio.CancelledError:
        if process is not None:
            await _kill(process)
        raise
    finally:
        if process is not None:
            await _kill(process)


async def _spawn_bash_command(
    command: str, config: LocalRuntimeAdapterConfig
) -> SpawnedShell:
    env = _shell_env(config.env)
    argv = _shell_argv(command, config, env)
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        cwd=config.cwd,
        env=env,
        start_new_session=sys.platform != "win32",
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


async def _communicate_bounded(
    process: asyncio.subprocess.Process,
) -> tuple[bytes, bool, bytes, bool]:
    stdout, stderr, _ = await asyncio.gather(
        _read_bounded(process.stdout), _read_bounded(process.stderr), process.wait()
    )
    return *stdout, *stderr


async def _read_bounded(reader: asyncio.StreamReader | None) -> tuple[bytes, bool]:
    if reader is None:
        return b"", False
    chunks: list[bytes] = []
    size = 0
    truncated = False
    while chunk := await reader.read(4096):
        remaining = MAX_OUTPUT_BYTES - size
        if remaining > 0:
            chunks.append(chunk[:remaining])
            size += min(len(chunk), remaining)
        if len(chunk) > remaining:
            truncated = True
    return b"".join(chunks), truncated


def _shell_env(extra_env: dict[str, str]) -> dict[str, str]:
    env = {
        **os.environ,
        "CI": "true",
        "NONINTERACTIVE": "1",
        "NO_TTY": "1",
        **extra_env,
    }
    if sys.platform == "win32":
        return {**env, "GIT_PAGER": "more", "PAGER": "more"}
    env.pop("LC_ALL", None)
    return {
        **env,
        "TERM": "dumb",
        "DEBIAN_FRONTEND": "noninteractive",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "LESS": "-FX",
        "LC_CTYPE": "C.UTF-8",
    }


def _decode_output(raw: bytes, encoding: str) -> str:
    return raw.decode(encoding, errors="replace")


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(process.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        process.terminate()
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        await process.wait()


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
