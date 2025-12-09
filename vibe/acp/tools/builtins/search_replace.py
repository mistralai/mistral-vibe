from __future__ import annotations

from pathlib import Path

from acp import ReadTextFileRequest, WriteTextFileRequest
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
from vibe.core.tools.builtins.search_replace import (
    SearchReplace as CoreSearchReplaceTool,
    SearchReplaceArgs,
    SearchReplaceResult,
    SearchReplaceState,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent


class AcpSearchReplaceState(SearchReplaceState, AcpToolState):
    file_backup_content: str | None = None


class SearchReplace(CoreSearchReplaceTool, BaseAcpTool[AcpSearchReplaceState]):
    state: AcpSearchReplaceState
    prompt_path = (
        VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "search_replace.md"
    )

    @classmethod
    def _get_tool_state_class(cls) -> type[AcpSearchReplaceState]:
        return AcpSearchReplaceState

    async def _read_file(self, file_path: Path) -> str:
        connection, session_id, _ = self._load_state()

        read_request = ReadTextFileRequest(sessionId=session_id, path=str(file_path))

        await self._send_in_progress_session_update()

        try:
            response = await connection.readTextFile(read_request)
        except Exception as e:
            raise ToolError(f"Unexpected error reading {file_path}: {e}") from e

        self.state.file_backup_content = response.content
        return response.content

    async def _backup_file(self, file_path: Path) -> None:
        if self.state.file_backup_content is None:
            return

        await self._write_file(
            file_path.with_suffix(file_path.suffix + ".bak"),
            self.state.file_backup_content,
        )

    async def _write_file(self, file_path: Path, content: str) -> None:
        connection, session_id, _ = self._load_state()

        write_request = WriteTextFileRequest(
            sessionId=session_id, path=str(file_path), content=content
        )

        try:
            await connection.writeTextFile(write_request)
        except Exception as e:
            raise ToolError(f"Error writing {file_path}: {e}") from e

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> SessionUpdate | None:
        args = event.args
        if not isinstance(args, SearchReplaceArgs):
            return None

        blocks = cls._parse_search_replace_blocks(args.content)

        return ToolCallStart(
            sessionUpdate="tool_call",
            title=cls.get_call_display(event).summary,
            toolCallId=event.tool_call_id,
            kind="edit",
            content=[
                FileEditToolCallContent(
                    type="diff",
                    path=args.file_path,
                    oldText=block.search,
                    newText=block.replace,
                )
                for block in blocks
            ],
            locations=[ToolCallLocation(path=args.file_path)],
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
        if not isinstance(result, SearchReplaceResult):
            return None

        blocks = cls._parse_search_replace_blocks(result.content)

        return ToolCallProgress(
            sessionUpdate="tool_call_update",
            toolCallId=event.tool_call_id,
            status="completed",
            content=[
                FileEditToolCallContent(
                    type="diff",
                    path=result.file,
                    oldText=block.search,
                    newText=block.replace,
                )
                for block in blocks
            ],
            locations=[ToolCallLocation(path=result.file)],
            rawOutput=result.model_dump_json(),
        )
