from __future__ import annotations

import asyncio
import shlex

from acp import CreateTerminalRequest, TerminalHandle
from acp.schema import (
    EnvVariable,
    TerminalToolCallContent,
    ToolCallProgress,
    ToolCallStart,
    WaitForTerminalExitResponse,
)

from vibe import VIBE_ROOT
from vibe.acp.tools.base import AcpToolState, BaseAcpTool
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.bash import Bash as CoreBashTool, BashArgs, BashResult
from vibe.core.types import ToolCallEvent, ToolResultEvent
from vibe.core.utils import logger


class AcpBashState(BaseToolState, AcpToolState):
    pass


class Bash(CoreBashTool, BaseAcpTool[AcpBashState]):
    prompt_path = VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "bash.md"
    state: AcpBashState

    @classmethod
    def _get_tool_state_class(cls) -> type[AcpBashState]:
        return AcpBashState

    async def run(self, args: BashArgs) -> BashResult:
        connection, session_id, _ = self._load_state()

        timeout = args.timeout or self.config.default_timeout
        max_bytes = self.config.max_output_bytes
        env, command, cmd_args = self._parse_command(args.command)

        create_request = CreateTerminalRequest(
            sessionId=session_id,
            command=command,
            args=cmd_args,
            env=env,
            cwd=str(self.config.effective_workdir),
            outputByteLimit=max_bytes,
        )

        try:
            terminal_handle = await connection.createTerminal(create_request)
        except Exception as e:
            raise ToolError(f"Failed to create terminal: {e!r}") from e

        await self._send_in_progress_session_update([
            TerminalToolCallContent(type="terminal", terminalId=terminal_handle.id)
        ])

        try:
            exit_response = await self._wait_for_terminal_exit(
                terminal_handle, timeout, args.command
            )

            output_response = await terminal_handle.current_output()

            return self._build_result(
                command=args.command,
                stdout=output_response.output,
                stderr="",
                returncode=exit_response.exitCode or 0,
            )

        finally:
            try:
                await terminal_handle.release()
            except Exception as e:
                logger.error(f"Failed to release terminal: {e!r}")

    def _parse_command(
        self, command_str: str
    ) -> tuple[list[EnvVariable], str, list[str]]:
        parts = shlex.split(command_str)
        env: list[EnvVariable] = []
        command: str = ""
        args: list[str] = []

        for part in parts:
            if "=" in part and not command:
                key, value = part.split("=", 1)
                env.append(EnvVariable(name=key, value=value))
            elif not command:
                command = part
            else:
                args.append(part)

        return env, command, args

    @classmethod
    def get_summary(cls, args: BashArgs) -> str:
        summary = f"{args.command}"
        if args.timeout:
            summary += f" (timeout {args.timeout}s)"

        return summary

    async def _wait_for_terminal_exit(
        self, terminal_handle: TerminalHandle, timeout: int, command: str
    ) -> WaitForTerminalExitResponse:
        try:
            return await asyncio.wait_for(
                terminal_handle.wait_for_exit(), timeout=timeout
            )
        except TimeoutError:
            try:
                await terminal_handle.kill()
            except Exception as e:
                logger.error(f"Failed to kill terminal: {e!r}")

            raise self._build_timeout_error(command, timeout)

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> ToolCallStart:
        if not isinstance(event.args, BashArgs):
            raise ValueError(f"Unexpected tool args: {event.args}")

        return ToolCallStart(
            sessionUpdate="tool_call",
            title=Bash.get_summary(event.args),
            content=None,
            toolCallId=event.tool_call_id,
            kind="execute",
            rawInput=event.args.model_dump_json(),
        )

    @classmethod
    def tool_result_session_update(
        cls, event: ToolResultEvent
    ) -> ToolCallProgress | None:
        return ToolCallProgress(
            sessionUpdate="tool_call_update",
            toolCallId=event.tool_call_id,
            status="failed" if event.error else "completed",
        )
