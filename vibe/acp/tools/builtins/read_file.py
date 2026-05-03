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
from vibe.acp.tools.base import (
    AcpToolState,
    BaseAcpTool,
    ToolCallSessionUpdateProtocol,
    ToolResultSessionUpdateProtocol,
)
from vibe.acp.tools.session_update import (
    failed_tool_result,
    fallback_tool_call,
    resolve_kind,
)
from vibe.core.tools.base import ToolError
from vibe.core.tools.builtins.read_file import (
    ReadFile as CoreReadFileTool,
    ReadFileArgs,
    ReadFileResult,
    ReadFileState,
    _ReadResult,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent

ReadFileResult = ReadFileResult


class AcpReadFileState(ReadFileState, AcpToolState):
    pass


class ReadFile(
    CoreReadFileTool,
    BaseAcpTool[AcpReadFileState],
    ToolCallSessionUpdateProtocol,
    ToolResultSessionUpdateProtocol,
):
    state: AcpReadFileState
    prompt_path = VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "read_file.md"

    @classmethod
    def _get_tool_state_class(cls) -> type[AcpReadFileState]:
        return AcpReadFileState

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> SessionUpdate | None:
        if not isinstance(event.args, ReadFileArgs):
            return fallback_tool_call(event, "read_file")

        resolved = str(Path(event.args.path).resolve())

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
        if failure := failed_tool_result(event, ReadFileResult):
            return failure

        result = event.result
        assert isinstance(result, ReadFileResult)
        resolved = str(Path(result.path).resolve())
        locations = [
            ToolCallLocation(
                path=resolved,
                field_meta={
                    "type": "file_range",
                    "offset": result.offset,
                    "limit": result.lines_read,
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

    async def _read_file(self, args: ReadFileArgs, file_path: Path) -> _ReadResult:
        client, session_id, _ = self._load_state()

        line = args.offset + 1 if args.offset > 0 else None
        limit = args.limit

        await self._send_in_progress_session_update()

        try:
            response = await client.read_text_file(
                session_id=session_id, path=str(file_path), line=line, limit=limit
            )
        except Exception as e:
            raise ToolError(f"Error reading {file_path}: {e}") from e

        content_lines = response.content.splitlines(keepends=True)
        lines_read = len(content_lines)
        bytes_read = sum(len(line.encode("utf-8")) for line in content_lines)

        was_truncated = args.limit is not None and lines_read >= args.limit

        return _ReadResult(
            lines=content_lines, bytes_read=bytes_read, was_truncated=was_truncated
        )
