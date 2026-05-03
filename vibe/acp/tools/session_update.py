from __future__ import annotations

from acp.helpers import SessionUpdate, ToolCallContentVariant
from acp.schema import (
    ContentToolCallContent,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolKind,
)
from pydantic import BaseModel

from vibe.acp.tools.base import (
    ToolCallSessionUpdateProtocol,
    ToolResultSessionUpdateProtocol,
)
from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.types import ToolCallEvent, ToolResultEvent
from vibe.core.utils import TaggedText, is_user_cancellation_event


def _cancellation_raw_output(event: ToolResultEvent) -> str | None:
    if event.skip_reason:
        return TaggedText.from_string(event.skip_reason).message
    if event.error:
        return TaggedText.from_string(event.error).message
    return None


TOOL_KIND_MAP: dict[str, ToolKind] = {
    "read_file": "read",
    "grep": "search",
    "web_search": "search",
    "web_fetch": "fetch",
    "write_file": "edit",
    "search_replace": "edit",
    "bash": "execute",
    "skill": "read",
}


def resolve_kind(tool_name: str) -> ToolKind:
    return TOOL_KIND_MAP.get(tool_name, "other")


def failed_tool_result(
    event: ToolResultEvent, expected_type: type[BaseModel]
) -> ToolCallProgress | None:
    """Return a failed ToolCallProgress if event is cancelled or has unexpected result type.

    Returns None when the result is valid (caller handles the success path).
    """
    kind = resolve_kind(event.tool_name)

    if is_user_cancellation_event(event):
        return ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=event.tool_call_id,
            status="failed",
            kind=kind,
            raw_output=_cancellation_raw_output(event),
            field_meta={"tool_name": event.tool_name},
        )

    if not isinstance(event.result, expected_type):
        return ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=event.tool_call_id,
            status="failed",
            kind=kind,
            raw_output=event.error or event.skip_reason,
            field_meta={"tool_name": event.tool_name},
        )

    return None


def fallback_tool_call(event: ToolCallEvent, title: str) -> ToolCallStart:
    """Default ToolCallStart when args are None or an unexpected type."""
    return ToolCallStart(
        session_update="tool_call",
        title=title,
        tool_call_id=event.tool_call_id,
        kind=resolve_kind(event.tool_name),
        raw_input=None,
        field_meta={"tool_name": event.tool_name},
    )


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
        kind=resolve_kind(event.tool_name),
        raw_input=event.args.model_dump_json() if event.args else None,
        field_meta={"tool_name": event.tool_name},
    )


def tool_result_session_update(event: ToolResultEvent) -> SessionUpdate | None:
    if is_user_cancellation_event(event):
        tool_status = "failed"
        if event.skip_reason:
            raw_output = TaggedText.from_string(event.skip_reason).message
        elif event.error:
            raw_output = TaggedText.from_string(event.error).message
        elif event.result:
            raw_output = event.result.model_dump_json()
        else:
            raw_output = None
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
        kind=resolve_kind(event.tool_name),
        raw_output=raw_output,
        content=content,
        field_meta={"tool_name": event.tool_name},
    )
