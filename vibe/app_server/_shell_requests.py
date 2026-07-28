from __future__ import annotations

import asyncio
from collections.abc import Callable
import time
from typing import Any

from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._execution import SessionExecution, SessionExecutionKind
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._shell import (
    ShellController,
    shell_effect_cancelled,
    shell_effect_detail,
    shell_effect_error,
    shell_effect_state,
)
from vibe.app_server._turns import TurnController
from vibe.app_server._utils import now_ms
from vibe.app_server.models import TextContentBlock
from vibe.app_server.protocol import (
    ContextInjectParams,
    ProtocolErrorCode,
    ShellInterruptParams,
    ShellInterruptResponse,
    ShellRunParams,
    ShellRunResponse,
)
from vibe.core.agent_loop import AgentLoop
from vibe.core.tools.builtins.bash import BashToolConfig
from vibe.core.types import ManualShellContext


class ShellRequestHandler:
    def __init__(
        self,
        agent_loop: AgentLoop,
        turns: TurnController,
        execution: SessionExecution,
        require_attached: Callable[[str], None],
    ) -> None:
        self._agent_loop = agent_loop
        self._turns = turns
        self._execution = execution
        self._require_attached = require_attached
        self._shell = ShellController(agent_loop.cwd)

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> DispatchResult:
        match method:
            case "shell/run":
                response: ProtocolModel = await self._run(
                    validate_wire(ShellRunParams, raw_params)
                )
            case "shell/interrupt":
                response = await self._interrupt(
                    validate_wire(ShellInterruptParams, raw_params)
                )
            case _:
                raise method_not_found(method)
        return DispatchResult(response)

    async def close(self) -> None:
        await self._shell.close()

    async def _run(self, params: ShellRunParams) -> ShellRunResponse:
        self._require_attached(params.session_id)
        if not params.command.strip():
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS, "Shell command cannot be empty"
            )
        execution = self._execution.begin(
            SessionExecutionKind.SHELL, params.operation_id
        )
        output: list[str] = []
        started_at = time.monotonic()
        created_at = now_ms()
        duration_ms = 0.0

        async def observe_output(chunk: str) -> None:
            output.append(chunk)
            await self._turns.append_effect_output(params.operation_id, chunk)

        try:
            await self._turns.start_effect(
                session_id=params.session_id,
                entry_id=params.operation_id,
                title="shell",
                detail=shell_effect_detail(params.command),
            )
            try:
                response = await self._shell.run(params, observe_output)
            except asyncio.CancelledError:
                await self._turns.complete_effect(
                    params.operation_id,
                    shell_effect_cancelled(
                        output_text="".join(output),
                        duration_ms=(time.monotonic() - started_at) * 1000,
                    ),
                )
                raise
            except Exception as exc:
                await self._turns.complete_effect(
                    params.operation_id,
                    shell_effect_error(
                        exc,
                        output_text="".join(output),
                        duration_ms=(time.monotonic() - started_at) * 1000,
                    ),
                )
                raise
            duration_ms = (time.monotonic() - started_at) * 1000
            await self._turns.complete_effect(
                params.operation_id,
                shell_effect_state(
                    response, output_text="".join(output), duration_ms=duration_ms
                ),
            )
        finally:
            self._execution.finish(execution)

        bash_config = self._agent_loop.tool_manager.get_tool_config("bash")
        max_output_bytes = (
            bash_config.max_output_bytes
            if isinstance(bash_config, BashToolConfig)
            else 16_000
        )
        await self._turns.inject(
            ContextInjectParams(
                session_id=params.session_id,
                input=[
                    TextContentBlock(
                        text=_manual_shell_context(
                            response, max_output_bytes=max_output_bytes
                        )
                    )
                ],
            ),
            manual_shell=ManualShellContext(
                operation_id=response.operation_id,
                command=response.command,
                cwd=response.cwd,
                stdout=response.stdout,
                stderr=response.stderr,
                output_text="".join(output),
                exit_code=response.exit_code,
                timed_out=response.timed_out,
                interrupted=response.interrupted,
                duration_ms=duration_ms,
                created_at=created_at,
            ),
        )
        return response

    async def _interrupt(self, params: ShellInterruptParams) -> ShellInterruptResponse:
        self._require_attached(params.session_id)
        interrupted = await self._shell.interrupt(params.operation_id)
        return ShellInterruptResponse(interrupted=interrupted)


def _manual_shell_context(result: ShellRunResponse, *, max_output_bytes: int) -> str:
    stdout = _cap_output(result.stdout, max_output_bytes)
    stderr = _cap_output(result.stderr, max_output_bytes)
    sections = [
        "Manual `!` command result from the user. Use this as context only.",
        f"Command: `{result.command}`",
        f"Working directory: `{result.cwd}`",
    ]
    if result.timed_out:
        sections.append("Status: timed out")
    elif result.interrupted:
        sections.append("Status: interrupted by user")
    else:
        sections.append(f"Exit code: {result.exit_code}")
    if stdout:
        sections.append(f"Stdout:\n```text\n{stdout.rstrip()}\n```")
    if stderr:
        sections.append(f"Stderr:\n```text\n{stderr.rstrip()}\n```")
    if not stdout and not stderr:
        sections.append("Output:\n```text\n(no output)\n```")
    return "\n\n".join(sections)


def _cap_output(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [truncated]"
