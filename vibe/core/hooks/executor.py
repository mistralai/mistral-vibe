from __future__ import annotations

import asyncio

from vibe.core.hooks.config import HookConfig
from vibe.core.hooks.models import HookExecutionResult, HookInvocation
from vibe.core.utils import kill_async_subprocess
from vibe.core.utils.io import decode_safe

_MAX_OUTPUT_BYTES = 1024 * 1024


async def _read_capped(
    stream: asyncio.StreamReader | None, limit: int = _MAX_OUTPUT_BYTES
) -> bytes:
    if stream is None:
        return b""
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = await stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
    return b"".join(chunks)


class HookExecutor:
    async def run(
        self, hook: HookConfig, invocation: HookInvocation
    ) -> HookExecutionResult:
        stdin_data = invocation.model_dump_json().encode()

        try:
            process = await asyncio.create_subprocess_shell(
                hook.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as e:
            return HookExecutionResult(
                hook_name=hook.name,
                exit_code=1,
                stdout="",
                stderr=f"Failed to start: {e}",
                timed_out=False,
            )

        try:
            stdin = process.stdin
            if stdin is None:
                await kill_async_subprocess(process)
                return HookExecutionResult(
                    hook_name=hook.name,
                    exit_code=1,
                    stdout="",
                    stderr="Failed to start: stdin stream unavailable",
                    timed_out=False,
                )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                self._run_process(process, stdin, stdin_data), timeout=hook.timeout
            )

            stdout = decode_safe(stdout_bytes, from_subprocess=True).text.strip()
            stderr = decode_safe(stderr_bytes, from_subprocess=True).text.strip()
            return HookExecutionResult(
                hook_name=hook.name,
                exit_code=process.returncode,
                stdout=stdout,
                stderr=stderr,
                timed_out=False,
            )
        except TimeoutError:
            await kill_async_subprocess(process)
            return HookExecutionResult(
                hook_name=hook.name,
                exit_code=None,
                stdout="",
                stderr="",
                timed_out=True,
            )
        except BaseException:
            if process.returncode is None:
                await kill_async_subprocess(process)
            raise

    async def _run_process(
        self,
        process: asyncio.subprocess.Process,
        stdin: asyncio.StreamWriter,
        stdin_data: bytes,
    ) -> tuple[bytes, bytes]:
        try:
            await self._write_stdin(stdin, stdin_data)
        except (BrokenPipeError, ConnectionResetError):
            pass

        stdout_bytes, stderr_bytes = await asyncio.gather(
            _read_capped(process.stdout), _read_capped(process.stderr)
        )
        await process.wait()
        return stdout_bytes, stderr_bytes

    async def _write_stdin(
        self, stdin: asyncio.StreamWriter, stdin_data: bytes
    ) -> None:
        stdin.write(stdin_data)
        try:
            await stdin.drain()
        finally:
            stdin.close()
