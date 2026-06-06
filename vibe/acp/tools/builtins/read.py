from __future__ import annotations

from pathlib import Path

from acp.helpers import SessionUpdate
from acp.schema import (
    ContentToolCallContent,
    TextContentBlock,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
)

from vibe import VIBE_ROOT
from vibe.acp.tools.base import AcpToolState, BaseAcpTool
from vibe.acp.tools.session_update import (
    ToolCallSessionUpdateProtocol,
    ToolResultSessionUpdateProtocol,
    failed_tool_result,
    fallback_tool_call,
    resolve_kind,
)
from vibe.core.tools.base import ToolError
from vibe.core.tools.builtins.read import (
    Read as CoreReadTool,
    ReadArgs,
    ReadResult,
    ReadState,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent


class AcpReadState(ReadState, AcpToolState):
    pass


class Read(
    CoreReadTool,
    BaseAcpTool[AcpReadState],
    ToolCallSessionUpdateProtocol,
    ToolResultSessionUpdateProtocol,
):
    state: AcpReadState
    prompt_path = VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "read.md"

    @classmethod
    def _get_tool_state_class(cls) -> type[AcpReadState]:
        return AcpReadState

    async def _read_file(
        self, args: ReadArgs, file_path: Path
    ) -> tuple[list[str], int | None, bool]:
        client, session_id = self._load_state()

        line = args.offset
        limit = args.limit

        try:
            response = await client.read_text_file(
                session_id=session_id, path=str(file_path), line=line, limit=limit + 1
            )
        except Exception as e:
            raise ToolError(f"Error reading {file_path}: {e}") from e

        lines = response.content.splitlines()
        total_lines = 0 if not response.content else None
        was_truncated = len(lines) > limit
        lines = lines[:limit]
        return lines, total_lines, was_truncated

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> SessionUpdate | None:
        if not isinstance(event.args, ReadArgs):
            return fallback_tool_call(event, "read")

        resolved = str(Path(event.args.file_path).resolve())

        return ToolCallStart(
            session_update="tool_call",
            title=cls.format_call_display(event.args).summary,
            tool_call_id=event.tool_call_id,
            kind=resolve_kind(event.tool_name),
            raw_input=event.args.model_dump_json(),
            locations=[
                ToolCallLocation(
                    path=resolved,
                    field_meta={
                        "type": "file_range",
                        "offset": event.args.offset,
                        "limit": event.args.limit,
                    },
                )
            ],
            field_meta={"tool_name": event.tool_name},
        )

    @classmethod
    def tool_result_session_update(cls, event: ToolResultEvent) -> SessionUpdate | None:
        if failure := failed_tool_result(event, ReadResult):
            return failure

        result = event.result
        assert isinstance(result, ReadResult)
        resolved = str(Path(result.file_path).resolve())
        locations = [
            ToolCallLocation(
                path=resolved,
                field_meta={
                    "type": "file_range",
                    "offset": result.start_line,
                    "limit": result.num_lines,
                },
            )
        ]

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
            raw_output=result.model_dump_json(),
            locations=locations,
            field_meta={"tool_name": event.tool_name},
        )
