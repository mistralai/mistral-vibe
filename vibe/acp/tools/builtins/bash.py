from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING

from acp.schema import (
    ContentToolCallContent,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    WaitForTerminalExitResponse,
)

from vibe import VIBE_ROOT
from vibe.acp.tools.base import AcpToolState, BaseAcpTool
from vibe.acp.tools.events import ToolTerminalOpenedEvent
from vibe.acp.tools.session_update import failed_tool_result, resolve_kind
from vibe.core.logger import logger
from vibe.core.tools.base import BaseToolState, InvokeContext, ToolError
from vibe.core.tools.builtins.bash import Bash as CoreBashTool, BashArgs, BashResult
from vibe.core.types import ToolCallEvent, ToolResultEvent, ToolStreamEvent

if TYPE_CHECKING:
    from vibe.core.config import AnyVibeConfig

_TERMINAL_CLEANUP_TIMEOUT = 10


class AcpBashState(BaseToolState, AcpToolState):
    pass


class Bash(CoreBashTool, BaseAcpTool[AcpBashState]):
    prompt_path = VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "bash.md"
    state: AcpBashState

    @classmethod
    def _get_tool_state_class(cls) -> type[AcpBashState]:
        return AcpBashState

    @classmethod
    def is_available(cls, config: AnyVibeConfig | None = None) -> bool:
        return not bool(config and config.experimental_bash_tool)

    async def run(
        self, args: BashArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BashResult, None]:
        client, session_id = self._load_state()

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

        try:
            if ctx is not None:
                yield ToolTerminalOpenedEvent(
                    tool_name=self.get_name(),
                    tool_call_id=ctx.tool_call_id,
                    terminal_id=terminal_id,
                )

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
                await asyncio.wait_for(
                    client.release_terminal(
                        session_id=session_id, terminal_id=terminal_id
                    ),
                    timeout=_TERMINAL_CLEANUP_TIMEOUT,
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
        client, session_id = self._load_state()

        try:
            return await asyncio.wait_for(
                client.wait_for_terminal_exit(
                    session_id=session_id, terminal_id=terminal_id
                ),
                timeout=timeout,
            )
        except TimeoutError:
            try:
                await asyncio.wait_for(
                    client.kill_terminal(
                        session_id=session_id, terminal_id=terminal_id
                    ),
                    timeout=_TERMINAL_CLEANUP_TIMEOUT,
                )
            except Exception as e:
                logger.error(f"Failed to kill terminal: {e!r}")

            raise self._build_timeout_error(command, timeout)

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> ToolCallStart | None:
        if event.args is None:
            # Title is left empty until args resolve so clients don't briefly
            # render the tool name (e.g. "bash") as a stand-in command while
            # the LLM is still streaming BashArgs. ACP requires title to be a
            # str, so we use "" as the "unknown yet" sentinel.
            return ToolCallStart(
                session_update="tool_call",
                title="",
                tool_call_id=event.tool_call_id,
                kind=resolve_kind(event.tool_name),
                content=None,
                raw_input=None,
                field_meta={"tool_name": event.tool_name},
            )
        if not isinstance(event.args, BashArgs):
            raise ValueError(f"Unexpected tool args: {event.args}")

        return ToolCallStart(
            session_update="tool_call",
            title=Bash.get_summary(event.args),
            content=None,
            tool_call_id=event.tool_call_id,
            kind=resolve_kind(event.tool_name),
            raw_input=event.args.model_dump_json(),
            field_meta={"tool_name": event.tool_name},
        )

    @classmethod
    def tool_result_session_update(
        cls, event: ToolResultEvent
    ) -> ToolCallProgress | None:
        if failure := failed_tool_result(event, BashResult):
            return failure

        return ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=event.tool_call_id,
            status="completed",
            content=[
                ContentToolCallContent(
                    type="content",
                    content=TextContentBlock(
                        type="text", text=cls.get_result_display(event).message
                    ),
                )
            ],
            kind=resolve_kind(event.tool_name),
            field_meta={"tool_name": event.tool_name},
        )
