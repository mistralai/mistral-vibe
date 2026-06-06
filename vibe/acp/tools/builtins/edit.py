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
from vibe.core.tools.builtins.edit import Edit as CoreEditTool, EditArgs, EditResult
from vibe.core.types import ToolCallEvent, ToolResultEvent
from vibe.core.utils.io import ReadSafeResult, normalize_newlines


class AcpEditState(BaseToolState, AcpToolState):
    pass


class Edit(CoreEditTool, BaseAcpTool[AcpEditState]):
    state: AcpEditState
    prompt_path = VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "edit.md"

    @classmethod
    def _get_tool_state_class(cls) -> type[AcpEditState]:
        return AcpEditState

    async def _read_file(self, file_path: Path) -> ReadSafeResult:
        client, session_id = self._load_state()

        try:
            response = await client.read_text_file(
                session_id=session_id, path=str(file_path)
            )
        except Exception as e:
            raise ToolError(f"Error reading {file_path}: {e}") from e

        text, newline = normalize_newlines(response.content)
        return ReadSafeResult(text, "utf-8", newline)

    async def _write_file(
        self, file_path: Path, content: str, encoding: str, newline: str
    ) -> None:
        client, session_id = self._load_state()

        try:
            await client.write_text_file(
                session_id=session_id,
                path=str(file_path),
                content=content.replace("\n", newline),
            )
        except Exception as e:
            raise ToolError(f"Error writing {file_path}: {e}") from e

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> SessionUpdate | None:
        if not isinstance(event.args, EditArgs):
            return fallback_tool_call(event, "edit")

        args = event.args

        return ToolCallStart(
            session_update="tool_call",
            title=cls.format_call_display(args).summary,
            tool_call_id=event.tool_call_id,
            kind=resolve_kind(event.tool_name),
            content=[
                FileEditToolCallContent(
                    type="diff",
                    path=args.file_path,
                    old_text=args.old_string,
                    new_text=args.new_string,
                )
            ],
            locations=[ToolCallLocation(path=str(Path(args.file_path).resolve()))],
            raw_input=args.model_dump_json(),
            field_meta={"tool_name": event.tool_name},
        )

    @classmethod
    def tool_result_session_update(cls, event: ToolResultEvent) -> SessionUpdate | None:
        if failure := failed_tool_result(event, EditResult):
            return failure

        result = event.result
        assert isinstance(result, EditResult)

        return ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=event.tool_call_id,
            status="completed",
            kind=resolve_kind(event.tool_name),
            content=[
                FileEditToolCallContent(
                    type="diff",
                    path=result.file,
                    old_text=result.old_string,
                    new_text=result.new_string,
                )
            ],
            locations=[ToolCallLocation(path=str(Path(result.file).resolve()))],
            raw_output=result.model_dump_json(),
            field_meta={"tool_name": event.tool_name},
        )
