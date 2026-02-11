from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from acp.schema import (
    TerminalToolCallContent,
    ToolCallProgress,
    ToolCallStart,
    WaitForTerminalExitResponse,
)

from vibe import VIBE_ROOT
from vibe.acp.tools.base import AcpToolState, BaseAcpTool
from vibe.core.tools.base import BaseToolState, InvokeContext, ToolError
from vibe.core.tools.builtins.bash import Bash as CoreBashTool, BashArgs, BashResult
from vibe.core.types import ToolCallEvent, ToolResultEvent, ToolStreamEvent
from vibe.core.utils import logger


class AcpBashState(BaseToolState, AcpToolState):
    pass


class Bash(CoreBashTool, BaseAcpTool[AcpBashState]):
    prompt_path = VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "bash.md"
    state: AcpBashState

    @classmethod
    def _get_tool_state_class(cls) -> type[AcpBashState]:
        return AcpBashState

    async def run(
        self, args: BashArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BashResult, None]:
        client, session_id, _ = self._load_state()

        timeout = args.timeout or self.config.default_timeout
        max_bytes = self.config.max_output_bytes

        try:
            terminal = await client.create_terminal(
                session_id=session_id,
                command=args.command,
                cwd=str(Path.cwd()),
                output_byte_limit=max_bytes,
            )
        except Exception as e:
            raise ToolError(f"Failed to create terminal: {e!r}") from e

        terminal_id = terminal.terminal_id

        await self._send_in_progress_session_update([
            TerminalToolCallContent(type="terminal", terminal_id=terminal_id)
        ])

        try:
            exit_response = await self._wait_for_terminal_exit(
                terminal_id=terminal_id, timeout=timeout, command=args.command
            )

            output_response = await client.terminal_output(
                session_id=session_id, terminal_id=terminal_id
            )

            yield self._build_result(
                command=args.command,
                stdout=output_response.output,
                stderr="",
                returncode=exit_response.exit_code or 0,
            )

        finally:
            try:
                await client.release_terminal(
                    session_id=session_id, terminal_id=terminal_id
                )
            except Exception as e:
                logger.error(f"Failed to release terminal: {e!r}")

    @classmethod
    def get_summary(cls, args: BashArgs) -> str:
        summary = f"{args.command}"
        if args.timeout:
            summary += f" (timeout {args.timeout}s)"

        return summary

    async def _wait_for_terminal_exit(
        self, terminal_id: str, timeout: int, command: str
    ) -> WaitForTerminalExitResponse:
        client, session_id, _ = self._load_state()

        try:
            return await asyncio.wait_for(
                client.wait_for_terminal_exit(
                    session_id=session_id, terminal_id=terminal_id
                ),
                timeout=timeout,
            )
        except TimeoutError:
            try:
                await client.kill_terminal(
                    session_id=session_id, terminal_id=terminal_id
                )
            except Exception as e:
                logger.error(f"Failed to kill terminal: {e!r}")

            raise self._build_timeout_error(command, timeout)

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> ToolCallStart:
        if not isinstance(event.args, BashArgs):
            raise ValueError(f"Unexpected tool args: {event.args}")

        return ToolCallStart(
            session_update="tool_call",
            title=Bash.get_summary(event.args),
            content=None,
            tool_call_id=event.tool_call_id,
            kind="execute",
            raw_input=event.args.model_dump_json(),
        )

    @classmethod
    def tool_result_session_update(
        cls, event: ToolResultEvent
    ) -> ToolCallProgress | None:
        return ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=event.tool_call_id,
            status="failed" if event.error else "completed",
        )
