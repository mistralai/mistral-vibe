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
from vibe.core.tools.builtins.search_replace import (
    SearchReplace as CoreSearchReplaceTool,
    SearchReplaceArgs,
    SearchReplaceResult,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent
from vibe.core.utils.io import ReadSafeResult


class AcpSearchReplaceState(BaseToolState, AcpToolState):
    file_backup_content: str | None = None
    file_backup_encoding: str = "utf-8"


class SearchReplace(CoreSearchReplaceTool, BaseAcpTool[AcpSearchReplaceState]):
    state: AcpSearchReplaceState
    prompt_path = (
        VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "search_replace.md"
    )

    @classmethod
    def _get_tool_state_class(cls) -> type[AcpSearchReplaceState]:
        return AcpSearchReplaceState

    async def _read_file(self, file_path: Path) -> ReadSafeResult:
        client, session_id, _ = self._load_state()

        await self._send_in_progress_session_update()

        try:
            response = await client.read_text_file(
                session_id=session_id, path=str(file_path)
            )
        except Exception as e:
            raise ToolError(f"Unexpected error reading {file_path}: {e}") from e

        self.state.file_backup_content = response.content
        self.state.file_backup_encoding = "utf-8"
        return ReadSafeResult(response.content, "utf-8")

    async def _backup_file(self, file_path: Path) -> None:
        if self.state.file_backup_content is None:
            return

        await self._write_file(
            file_path.with_suffix(file_path.suffix + ".bak"),
            self.state.file_backup_content,
            self.state.file_backup_encoding,
        )

    async def _write_file(self, file_path: Path, content: str, encoding: str) -> None:
        client, session_id, _ = self._load_state()

        try:
            await client.write_text_file(
                session_id=session_id, path=str(file_path), content=content
            )
        except Exception as e:
            raise ToolError(f"Error writing {file_path}: {e}") from e

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> SessionUpdate | None:
        if not isinstance(event.args, SearchReplaceArgs):
            return fallback_tool_call(event, "search_replace")

        args = event.args

        blocks = cls._parse_search_replace_blocks(args.content)

        return ToolCallStart(
            session_update="tool_call",
            title=cls.get_call_display(event).summary,
            tool_call_id=event.tool_call_id,
            kind=resolve_kind(event.tool_name),
            content=[
                FileEditToolCallContent(
                    type="diff",
                    path=args.file_path,
                    old_text=block.search,
                    new_text=block.replace,
                )
                for block in blocks
            ],
            locations=[ToolCallLocation(path=str(Path(args.file_path).resolve()))],
            raw_input=args.model_dump_json(),
            field_meta={"tool_name": event.tool_name},
        )

    @classmethod
    def tool_result_session_update(cls, event: ToolResultEvent) -> SessionUpdate | None:
        if failure := failed_tool_result(event, SearchReplaceResult):
            return failure

        result = event.result
        assert isinstance(result, SearchReplaceResult)

        blocks = cls._parse_search_replace_blocks(result.content)

        return ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=event.tool_call_id,
            status="completed",
            kind=resolve_kind(event.tool_name),
            content=[
                FileEditToolCallContent(
                    type="diff",
                    path=result.file,
                    old_text=block.search,
                    new_text=block.replace,
                )
                for block in blocks
            ],
            locations=[ToolCallLocation(path=str(Path(result.file).resolve()))],
            raw_output=result.model_dump_json(),
            field_meta={"tool_name": event.tool_name},
        )
