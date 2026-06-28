from __future__ import annotations

from acp.helpers import SessionUpdate
from acp.schema import (
    ContentToolCallContent,
    TextContentBlock,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
)

from vibe import VIBE_ROOT
from vibe.acp.tools.session_update import (
    ToolCallSessionUpdateProtocol,
    ToolResultSessionUpdateProtocol,
    failed_tool_result,
    fallback_tool_call,
    resolve_kind,
)
from vibe.core.tools.builtins.glob import Glob as CoreGlobTool, GlobArgs, GlobResult
from vibe.core.types import ToolCallEvent, ToolResultEvent


class Glob(
    CoreGlobTool, ToolCallSessionUpdateProtocol, ToolResultSessionUpdateProtocol
):
    prompt_path = VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "glob.md"

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> SessionUpdate | None:
        if not isinstance(event.args, GlobArgs):
            return fallback_tool_call(event, "glob")

        return ToolCallStart(
            session_update="tool_call",
            title=cls.get_call_display(event).summary,
            tool_call_id=event.tool_call_id,
            kind=resolve_kind(event.tool_name),
            raw_input=event.args.model_dump_json(),
            field_meta={"tool_name": event.tool_name, "query": event.args.pattern},
        )

    @classmethod
    def tool_result_session_update(cls, event: ToolResultEvent) -> SessionUpdate | None:
        if failure := failed_tool_result(event, GlobResult):
            return failure

        result = event.result
        assert isinstance(result, GlobResult)

        locations = [ToolCallLocation(path=path) for path in result.matches]

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
            locations=locations if locations else None,
            field_meta={"tool_name": event.tool_name},
        )
