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

TOOL_KIND: dict[str, ToolKind] = {
    "grep": "search",
    "read_file": "read",
    # Right now, jetbrains implementation of "edit" tool kind is broken
    # Leading to the tool not appearing in the chat
    # "write_file": "edit",
    # "search_replace": "edit",
}


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
        session_update="tool_call",
        title=display.summary,
        content=content,
        tool_call_id=event.tool_call_id,
        kind=TOOL_KIND.get(event.tool_name, "other"),
        raw_input=event.args.model_dump_json() if event.args else None,
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
            session_update="tool_call_update",
            tool_call_id=event.tool_call_id,
            status="failed",
            raw_output=raw_output,
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
        session_update="tool_call_update",
        tool_call_id=event.tool_call_id,
        status=tool_status,
        raw_output=raw_output,
        content=content,
    )
