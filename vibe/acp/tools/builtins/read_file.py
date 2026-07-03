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
from vibe.core.tools.builtins.read_file import (
    DEFAULT_LINE_LIMIT,
    ReadFile as CoreReadTool,
    ReadFileArgs,
    ReadFileResult,
    ReadFileState,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent


class AcpReadFileState(ReadFileState, AcpToolState):
    pass


class ReadFile(
    CoreReadTool,
    BaseAcpTool[AcpReadFileState],
    ToolCallSessionUpdateProtocol,
    ToolResultSessionUpdateProtocol,
):
    state: AcpReadFileState
    prompt_path = VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "read_file.md"

    @classmethod
    def _get_tool_state_class(cls) -> type[AcpReadFileState]:
        return AcpReadFileState

    async def _read_file(
        self, args: ReadFileArgs, file_path: Path
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
        if not isinstance(event.args, ReadFileArgs):
            return fallback_tool_call(event, "read_file")

        resolved = str(Path(event.args.file_path).resolve())

        return ToolCallStart(
            session_update="tool_call",
            title=cls.format_call_display(event.args).summary,
            tool_call_id=event.tool_call_id,
            kind=resolve_kind(event.tool_name),
            raw_input=event.args.model_dump_json(),
            locations=[cls._call_location(resolved, event.args)],
            field_meta={"tool_name": event.tool_name},
        )

    @staticmethod
    def _call_location(resolved: str, args: ReadFileArgs) -> ToolCallLocation:
        # Only range explicitly bounded reads; a whole-file read's default
        # limit would render a misleading "L1-L<default>" chip.
        if args.limit != DEFAULT_LINE_LIMIT:
            return ToolCallLocation(
                path=resolved,
                field_meta={
                    "type": "file_range",
                    "offset": args.offset,
                    "limit": args.limit,
                },
            )
        return ToolCallLocation(
            path=resolved, line=args.offset, field_meta={"type": "file"}
        )

    @staticmethod
    def _result_location(resolved: str, result: ReadFileResult) -> ToolCallLocation:
        # Mirror the start-event logic: a whole-file read points at the file with
        # no line (requested_offset is None), so it renders "Read foo.ts" rather
        # than a stray "L1". A default-limit read that got truncated only read
        # part of the file, so surface the real range instead of implying the
        # whole file was read. num_lines is already clamped to the lines read.
        bounded = result.requested_limit != DEFAULT_LINE_LIMIT or result.was_truncated
        if bounded:
            return ToolCallLocation(
                path=resolved,
                field_meta={
                    "type": "file_range",
                    "offset": result.start_line,
                    "limit": result.num_lines,
                },
            )
        return ToolCallLocation(
            path=resolved, line=result.requested_offset, field_meta={"type": "file"}
        )

    @classmethod
    def tool_result_session_update(cls, event: ToolResultEvent) -> SessionUpdate | None:
        if failure := failed_tool_result(event, ReadFileResult):
            return failure

        result = event.result
        assert isinstance(result, ReadFileResult)
        resolved = str(Path(result.file_path).resolve())
        locations = [cls._result_location(resolved, result)]

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
