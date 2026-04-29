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
from vibe.acp.tools.session_update import (
    failed_tool_result,
    fallback_tool_call,
    resolve_kind,
)
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.write_file import (
    WriteFile as CoreWriteFileTool,
    WriteFileArgs,
    WriteFileResult,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent


class AcpWriteFileState(BaseToolState, AcpToolState):
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
        if not isinstance(event.args, WriteFileArgs):
            return fallback_tool_call(event, "write_file")

        return ToolCallStart(
            session_update="tool_call",
            title=cls.format_call_display(event.args).summary,
            tool_call_id=event.tool_call_id,
            kind=resolve_kind(event.tool_name),
            content=[
                FileEditToolCallContent(
                    type="diff",
                    path=event.args.path,
                    old_text=None,
                    new_text=event.args.content,
                )
            ],
            locations=[ToolCallLocation(path=str(Path(event.args.path).resolve()))],
            raw_input=event.args.model_dump_json(),
            field_meta={"tool_name": event.tool_name},
        )

    @classmethod
    def tool_result_session_update(cls, event: ToolResultEvent) -> SessionUpdate | None:
        if failure := failed_tool_result(event, WriteFileResult):
            return failure

        result = event.result
        assert isinstance(result, WriteFileResult)

        return ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=event.tool_call_id,
            status="completed",
            kind=resolve_kind(event.tool_name),
            content=[
                FileEditToolCallContent(
                    type="diff",
                    path=result.path,
                    old_text=None,
                    new_text=result.content,
                )
            ],
            locations=[ToolCallLocation(path=str(Path(result.path).resolve()))],
            raw_output=result.model_dump_json(),
            field_meta={"tool_name": event.tool_name},
        )
