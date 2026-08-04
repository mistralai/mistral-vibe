from __future__ import annotations

import asyncio
import codecs
from collections.abc import Awaitable, Callable
from pathlib import Path

from vibe.app_server.models import (
    CancelledEffectState,
    CompletedEffectState,
    EffectCallDisplay,
    EffectDetail,
    EffectResultDisplay,
    EffectState,
    FailedEffectState,
    PublicError,
    ShellEffectDetail,
    ShellEffectInput,
)
from vibe.app_server.protocol import ShellRunParams, ShellRunResponse
from vibe.core.types import ManualShellContext
from vibe.core.utils import kill_async_subprocess
from vibe.core.utils.shell import spawn_shell_command

type ShellOutputObserver = Callable[[str], Awaitable[None]]


class ShellConflictError(RuntimeError):
    pass


def shell_effect_detail(command: str) -> EffectDetail:
    return ShellEffectDetail(
        tool_name="shell",
        input=ShellEffectInput(command=command),
        display=EffectCallDisplay(
            summary=f"shell: {command}",
            verb="Running",
            message=command,
            settled_verb="Ran",
            settled_message=command,
            status_text="Running command",
        ),
    )


def shell_effect_state(
    result: ShellRunResponse, *, output_text: str, duration_ms: float
) -> EffectState:
    if result.interrupted:
        return CancelledEffectState(
            reason="Command interrupted",
            output_text=output_text,
            duration_ms=duration_ms,
            display=EffectResultDisplay(success=False, message="Command interrupted"),
        )

    message = f"Ran {result.command}"
    if result.timed_out:
        message = "Command timed out"
    elif result.exit_code != 0:
        message = f"Command exited with status {result.exit_code}"

    display = EffectResultDisplay(success=result.exit_code == 0, message=message)
    if result.timed_out or result.exit_code != 0:
        return FailedEffectState(
            error=PublicError(message=message),
            output_text=output_text,
            duration_ms=duration_ms,
            display=display,
        )
    return CompletedEffectState(
        output={"stdout": result.stdout, "stderr": result.stderr},
        output_text=output_text,
        duration_ms=duration_ms,
        display=display,
    )


def shell_effect_error(
    error: Exception, *, output_text: str, duration_ms: float
) -> FailedEffectState:
    message = str(error) or type(error).__name__
    return FailedEffectState(
        error=PublicError(message=message),
        output_text=output_text,
        duration_ms=duration_ms,
        display=EffectResultDisplay(success=False, message=message),
    )


def shell_effect_cancelled(
    *, output_text: str, duration_ms: float
) -> CancelledEffectState:
    message = "Command interrupted"
    return CancelledEffectState(
        reason=message,
        output_text=output_text,
        duration_ms=duration_ms,
        display=EffectResultDisplay(success=False, message=message),
    )


def restored_shell_effect_state(context: ManualShellContext) -> EffectState:
    return shell_effect_state(
        ShellRunResponse(
            operation_id=context.operation_id,
            command=context.command,
            cwd=context.cwd,
            stdout=context.stdout,
            stderr=context.stderr,
            exit_code=context.exit_code,
            timed_out=context.timed_out,
            interrupted=context.interrupted,
        ),
        output_text=context.output_text,
        duration_ms=context.duration_ms,
    )


class ShellController:
    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._operations: set[str] = set()
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._interrupted: set[str] = set()

    async def run(
        self, params: ShellRunParams, observe_output: ShellOutputObserver | None = None
    ) -> ShellRunResponse:
        if params.operation_id in self._operations:
            raise ShellConflictError(
                f"Shell operation already exists: {params.operation_id}"
            )

        self._operations.add(params.operation_id)
        process: asyncio.subprocess.Process | None = None
        stdout: list[str] = []
        stderr: list[str] = []
        readers: list[asyncio.Task[None]] = []
        timed_out = False
        interrupted = False
        try:
            process = await spawn_shell_command(params.command, cwd=self._cwd)
            self._processes[params.operation_id] = process
            readers = [
                asyncio.create_task(
                    self._read_stream(process.stdout, stdout, observe_output)
                ),
                asyncio.create_task(
                    self._read_stream(process.stderr, stderr, observe_output)
                ),
            ]
            if params.operation_id in self._interrupted:
                await kill_async_subprocess(process)
            else:
                try:
                    await asyncio.wait_for(
                        process.wait(), timeout=params.timeout_seconds
                    )
                except TimeoutError:
                    timed_out = True
                    await kill_async_subprocess(process)
            await asyncio.gather(*readers)
        except BaseException:
            if process is not None:
                await kill_async_subprocess(process)
            raise
        finally:
            for reader in readers:
                if not reader.done():
                    reader.cancel()
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)
            interrupted = params.operation_id in self._interrupted
            self._interrupted.discard(params.operation_id)
            self._processes.pop(params.operation_id, None)
            self._operations.discard(params.operation_id)

        if process is None:
            raise RuntimeError("Shell process was not started")
        return ShellRunResponse(
            operation_id=params.operation_id,
            command=params.command,
            cwd=str(self._cwd),
            stdout="".join(stdout),
            stderr="".join(stderr),
            exit_code=(1 if timed_out or interrupted else process.returncode or 0),
            timed_out=timed_out,
            interrupted=interrupted,
        )

    async def interrupt(self, operation_id: str) -> bool:
        if operation_id not in self._operations:
            return False
        self._interrupted.add(operation_id)
        if process := self._processes.get(operation_id):
            await kill_async_subprocess(process)
        return True

    async def close(self) -> None:
        for operation_id in list(self._operations):
            await self.interrupt(operation_id)

    async def _read_stream(
        self,
        stream: asyncio.StreamReader | None,
        parts: list[str],
        observe_output: ShellOutputObserver | None,
    ) -> None:
        if stream is None:
            return
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while chunk := await stream.read(4096):
            text = decoder.decode(chunk)
            if text:
                parts.append(text)
                if observe_output is not None:
                    await observe_output(text)
        if text := decoder.decode(b"", final=True):
            parts.append(text)
            if observe_output is not None:
                await observe_output(text)
