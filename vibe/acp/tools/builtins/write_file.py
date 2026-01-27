from __future__ import annotations

from pathlib import Path

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
        client, session_id, _ = self._load_state()

        await self._send_in_progress_session_update()

        try:
            await client.write_text_file(
                session_id=session_id, path=str(file_path), content=args.content
            )
        except Exception as e:
            raise ToolError(f"Error writing {file_path}: {e}") from e

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> SessionUpdate | None:
        args = event.args
        if not isinstance(args, WriteFileArgs):
            return None

        return ToolCallStart(
            session_update="tool_call",
            title=cls.get_call_display(event).summary,
            tool_call_id=event.tool_call_id,
            kind="edit",
            content=[
                FileEditToolCallContent(
                    type="diff", path=args.path, old_text=None, new_text=args.content
                )
            ],
            locations=[ToolCallLocation(path=args.path)],
            raw_input=args.model_dump_json(),
        )

    @classmethod
    def tool_result_session_update(cls, event: ToolResultEvent) -> SessionUpdate | None:
        if event.error:
            return ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id=event.tool_call_id,
                status="failed",
            )

        result = event.result
        if not isinstance(result, WriteFileResult):
            return None

        return ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=event.tool_call_id,
            status="completed",
            content=[
                FileEditToolCallContent(
                    type="diff",
                    path=result.path,
                    old_text=None,
                    new_text=result.content,
                )
            ],
            locations=[ToolCallLocation(path=result.path)],
            raw_output=result.model_dump_json(),
        )
