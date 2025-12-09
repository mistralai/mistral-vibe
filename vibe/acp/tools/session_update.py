from __future__ import annotations

from acp.helpers import SessionUpdate, ToolCallContentVariant
from acp.schema import (
    ContentToolCallContent,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolKind,
)

from vibe.acp.tools.base import (
    ToolCallSessionUpdateProtocol,
    ToolResultSessionUpdateProtocol,
)
from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.types import ToolCallEvent, ToolResultEvent
from vibe.core.utils import TaggedText, is_user_cancellation_event

TOOL_KIND: dict[str, ToolKind] = {"read_file": "read", "grep": "search"}


def tool_call_session_update(event: ToolCallEvent) -> SessionUpdate | None:
    if issubclass(event.tool_class, ToolCallSessionUpdateProtocol):
        return event.tool_class.tool_call_session_update(event)

    adapter = ToolUIDataAdapter(event.tool_class)
    display = adapter.get_call_display(event)
    content: list[ToolCallContentVariant] | None = (
        [
            ContentToolCallContent(
                type="content",
                content=TextContentBlock(type="text", text=display.content),
            )
        ]
        if display.content
        else None
    )

    return ToolCallStart(
        sessionUpdate="tool_call",
        title=display.summary,
        content=content,
        toolCallId=event.tool_call_id,
        kind=TOOL_KIND.get(event.tool_name, "other"),
        rawInput=event.args.model_dump_json(),
    )


def tool_result_session_update(event: ToolResultEvent) -> SessionUpdate | None:
    if is_user_cancellation_event(event):
        tool_status = "failed"
        raw_output = (
            TaggedText.from_string(event.skip_reason).message
            if event.skip_reason
            else None
        )
    elif event.result:
        tool_status = "completed"
        raw_output = event.result.model_dump_json()
    else:
        tool_status = "failed"
        raw_output = (
            TaggedText.from_string(event.error).message if event.error else None
        )

    if event.tool_class is None:
        return ToolCallProgress(
            sessionUpdate="tool_call_update",
            toolCallId=event.tool_call_id,
            status="failed",
            rawOutput=raw_output,
            content=[
                ContentToolCallContent(
                    type="content",
                    content=TextContentBlock(type="text", text=raw_output or ""),
                )
            ],
        )

    if issubclass(event.tool_class, ToolResultSessionUpdateProtocol):
        return event.tool_class.tool_result_session_update(event)

    if tool_status == "failed":
        content = [
            ContentToolCallContent(
                type="content",
                content=TextContentBlock(type="text", text=raw_output or ""),
            )
        ]
    else:
        adapter = ToolUIDataAdapter(event.tool_class)
        display = adapter.get_result_display(event)
        content: list[ToolCallContentVariant] | None = (
            [
                ContentToolCallContent(
                    type="content",
                    content=TextContentBlock(type="text", text=display.message),
                )
            ]
            if display.message
            else None
        )

    return ToolCallProgress(
        sessionUpdate="tool_call_update",
        toolCallId=event.tool_call_id,
        status=tool_status,
        rawOutput=raw_output,
        content=content,
    )
