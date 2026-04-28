from __future__ import annotations

import asyncio

from vibe.core.hooks.config import HookConfig
from vibe.core.hooks.models import HookExecutionResult, HookInvocation
from vibe.core.utils import kill_async_subprocess
from vibe.core.utils.io import decode_safe


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
                stdout=f"Failed to start: {e}",
                stderr="",
                timed_out=False,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=stdin_data), timeout=hook.timeout
            )
            stdout = decode_safe(stdout_bytes).text.strip()
            stderr = decode_safe(stderr_bytes).text.strip()
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
