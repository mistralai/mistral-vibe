from __future__ import annotations

from pathlib import Path

from acp import WriteTextFileRequest
from acp.helpers import SessionUpdate
from acp.schema import (
    FileEditToolCallContent,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
)

from vibe import VIBE_ROOT
from vibe.acp.tools.base import AcpToolState, BaseAcpTool
from vibe.core.tools.base import ToolError
from vibe.core.tools.builtins.write_file import (
    WriteFile as CoreWriteFileTool,
    WriteFileArgs,
    WriteFileResult,
    WriteFileState,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent


class AcpWriteFileState(WriteFileState, AcpToolState):
    pass


class WriteFile(CoreWriteFileTool, BaseAcpTool[AcpWriteFileState]):
    state: AcpWriteFileState
    prompt_path = (
        VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "write_file.md"
    )

    @classmethod
    def _get_tool_state_class(cls) -> type[AcpWriteFileState]:
        return AcpWriteFileState

    async def _write_file(self, args: WriteFileArgs, file_path: Path) -> None:
        connection, session_id, _ = self._load_state()

        write_request = WriteTextFileRequest(
            sessionId=session_id, path=str(file_path), content=args.content
        )

        await self._send_in_progress_session_update()

        try:
            await connection.writeTextFile(write_request)
        except Exception as e:
            raise ToolError(f"Error writing {file_path}: {e}") from e

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> SessionUpdate | None:
        args = event.args
        if not isinstance(args, WriteFileArgs):
            return None

        return ToolCallStart(
            sessionUpdate="tool_call",
            title=cls.get_call_display(event).summary,
            toolCallId=event.tool_call_id,
            kind="edit",
            content=[
                FileEditToolCallContent(
                    type="diff", path=args.path, oldText=None, newText=args.content
                )
            ],
            locations=[ToolCallLocation(path=args.path)],
            rawInput=args.model_dump_json(),
        )

    @classmethod
    def tool_result_session_update(cls, event: ToolResultEvent) -> SessionUpdate | None:
        if event.error:
            return ToolCallProgress(
                sessionUpdate="tool_call_update",
                toolCallId=event.tool_call_id,
                status="failed",
            )

        result = event.result
        if not isinstance(result, WriteFileResult):
            return None

        return ToolCallProgress(
            sessionUpdate="tool_call_update",
            toolCallId=event.tool_call_id,
            status="completed",
            content=[
                FileEditToolCallContent(
                    type="diff", path=result.path, oldText=None, newText=result.content
                )
            ],
            locations=[ToolCallLocation(path=result.path)],
            rawOutput=result.model_dump_json(),
        )
