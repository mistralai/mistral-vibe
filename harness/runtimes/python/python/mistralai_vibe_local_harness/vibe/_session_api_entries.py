# ruff: noqa: I002 -- new modules here do without postponed annotations.
"""Convert the Runtime's public history entries to Session API history entries.

The functions keep no state. The one thing they change is the ``HistoryIndex``
they are given, which assigns positions to an entry seen for the first time.
"""

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

from pydantic import JsonValue

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    CheckpointHistoryEntry,
    ContentBlock,
    EmbeddedResourceContentBlock,
    HistoryEntry,
    ImageContentBlock,
    MessageHistoryEntry,
    MessageRole,
    NoticeHistoryEntry,
    NoticeLevel,
    ProtocolError,
    ReasoningHistoryEntry,
    ResourceLinkContentBlock,
    TextContentBlock,
    ToolCallHistoryEntry,
    ToolResultHistoryEntry,
    UnknownHistoryEntry,
)
from mistralai_vibe_local_harness.session_protocol import JsonObject

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.vibe._session_api_state import HistoryIndex


VIBE_ANNOTATION_PREFIX = "vibe."
"""Prefix of the annotations that carry Vibe display data the API has no field for."""

_NOTICE_LEVELS: dict[object, NoticeLevel] = {
    "info": "info",
    "warning": "warning",
    "error": "error",
}
_MESSAGE_ROLES: dict[object, MessageRole] = {
    "system": "system",
    "user": "user",
    "assistant": "assistant",
}
TOOL_CALL_SUFFIX = ":call"
"""Suffix of the Session API entry a tool record's call becomes."""
TOOL_RESULT_SUFFIX = ":result"
"""Suffix of the Session API entry a tool record's result becomes."""
CALLBACK_ENTRY_FIELDS = ("callbackId", "title", "detail", "state")
"""The fields of a callback entry kept beside the shared ones when it is converted."""


def _content_blocks(raw_content: object) -> list[ContentBlock]:
    if not isinstance(raw_content, list):
        return []
    return [block for raw in raw_content if (block := _content_block(raw)) is not None]


def convert_history_entry(
    raw: JsonObject, history_index: "HistoryIndex", tool_calls: Mapping[str, JsonObject]
) -> list[HistoryEntry]:
    """The Session API entries for one public history entry.

    A tool record becomes a call and a result; a type with no Session API form
    becomes none.
    """
    entry_type = raw.get("type")
    entry_id = cast(str, raw["id"])
    if entry_type == "effect":
        return _tool_entries(raw, entry_id, history_index, tool_calls)
    build = ENTRY_BUILDERS.get(cast(str, entry_type))
    if build is None:
        return []
    return [build(raw, _base_fields(raw, history_index.position(entry_id)))]


def _reasoning_entry(raw: JsonObject, base: dict[str, Any]) -> HistoryEntry:
    if raw.get("outcome") is not None:
        base["annotations"] = {
            **base["annotations"],
            VIBE_ANNOTATION_PREFIX + "outcome": cast(JsonValue, raw["outcome"]),
        }
    return ReasoningHistoryEntry(
        **base,
        text=optional_str(raw.get("text")) or "",
        summary=[item for item in _list(raw.get("summary")) if isinstance(item, str)],
    )


def _notice_entry(raw: JsonObject, base: dict[str, Any]) -> HistoryEntry:
    # Projected notices hold ``detail``; a scheduled-loop notice the Session
    # writes itself holds ``details``. The annotation keeps which one it was.
    key = "details" if "details" in raw and "detail" not in raw else "detail"
    annotations = dict(base.pop("annotations"))
    if key == "details":
        annotations[VIBE_ANNOTATION_PREFIX + "noticeDetailsKey"] = key
    return NoticeHistoryEntry(
        **base,
        annotations=annotations,
        level=_NOTICE_LEVELS.get(raw.get("level"), "info"),
        message=optional_str(raw.get("message")) or "",
        details=cast(JsonValue, raw.get(key)),
    )


def _message_entry(raw: JsonObject, base: dict[str, Any]) -> HistoryEntry:
    role = raw.get("role")
    annotations = dict(base.pop("annotations"))
    for key in ("source", "userDisplayContent", "outcome"):
        if raw.get(key) is not None:
            annotations[VIBE_ANNOTATION_PREFIX + key] = cast(JsonValue, raw[key])
    return MessageHistoryEntry(
        **base,
        annotations=annotations,
        role=_MESSAGE_ROLES.get(role, "assistant"),
        content=_content_blocks(raw.get("content")),
    )


def _checkpoint_entry(raw: JsonObject, base: dict[str, Any]) -> HistoryEntry:
    kind = raw.get("kind")
    api_kind = "compaction" if kind == "compaction" else "custom"
    annotations = dict(base.pop("annotations"))
    if api_kind == "custom" and isinstance(kind, str):
        annotations[VIBE_ANNOTATION_PREFIX + "checkpointKind"] = kind
    return CheckpointHistoryEntry(
        **base,
        annotations=annotations,
        checkpoint_kind=api_kind,
        message=optional_str(raw.get("message")),
        details=cast(JsonValue, raw.get("details")),
    )


def _callback_entry(raw: JsonObject, base: dict[str, Any]) -> HistoryEntry:
    # The Session API has no callback entry, so a settled question kept in
    # history travels as an entry of an unknown type, with its fields as is.
    return UnknownHistoryEntry.model_validate({
        **base,
        "type": "callback",
        **{key: raw.get(key) for key in CALLBACK_ENTRY_FIELDS},
    })


ENTRY_BUILDERS: dict[str, Callable[[JsonObject, dict[str, Any]], HistoryEntry]] = {
    "message": _message_entry,
    "reasoning": _reasoning_entry,
    "checkpoint": _checkpoint_entry,
    "notice": _notice_entry,
    "callback": _callback_entry,
}
"""The builder of each public entry type that becomes one Session API entry."""


def _tool_entries(
    raw: JsonObject,
    entry_id: str,
    history_index: "HistoryIndex",
    tool_calls: Mapping[str, JsonObject],
) -> list[HistoryEntry]:
    call_index, result_index = history_index.reserve_tool(entry_id)
    detail = raw.get("detail")
    detail = detail if isinstance(detail, Mapping) else {}
    state = raw.get("state")
    state = state if isinstance(state, Mapping) else {}
    name = optional_str(detail.get("toolName")) or optional_str(raw.get("title"))
    name = name or "tool"
    # The public record holds only the action ID and the display input; the
    # model's own call ID and full arguments come from the kept tool call.
    tool_call_id = model_tool_call_id(entry_id, tool_calls)
    arguments = tool_calls.get(entry_id, {}).get("arguments", detail.get("input"))
    created_at = _int(raw.get("createdAt"))
    updated_at = _int(raw.get("updatedAt"), default=created_at)
    turn_id = optional_str(raw.get("turnId"))
    status = state.get("status")
    finished = raw.get("generationStatus") == "completed" or status in {
        "completed",
        "failed",
        "skipped",
        "cancelled",
        "denied",
    }
    error = state.get("error")
    result_error = (
        ProtocolError(
            message=optional_str(error.get("message")) or "Tool failed",
            code=optional_str(error.get("code")),
            details=cast(JsonValue, error.get("details")),
        )
        if isinstance(error, Mapping)
        else (
            ProtocolError(
                message=optional_str(state.get("reason")) or str(status),
                code=f"tool_{status}",
            )
            if status in {"skipped", "cancelled", "denied"}
            else None
        )
    )
    # The result value carries the output, except when the tool failed: the
    # API holds an error or a result, so a failed tool's output object (its
    # approval metadata and error) stays in the Vibe display annotation.
    display: JsonObject = {
        key: cast(JsonValue, value)
        for key, value in (
            ("detail", detail),
            (
                "state",
                state
                if result_error is not None
                else {k: v for k, v in state.items() if k != "output"},
            ),
            ("relatedEntryId", raw.get("relatedEntryId")),
            ("inputEntryId", raw.get("inputEntryId")),
            ("title", raw.get("title")),
        )
    }
    return [
        ToolCallHistoryEntry(
            id=entry_id + TOOL_CALL_SUFFIX,
            index=call_index,
            turn_id=turn_id,
            generation_status="complete",
            created_at=created_at,
            updated_at=created_at,
            tool_call_id=tool_call_id,
            tool_kind="local",
            name=name,
            arguments=cast(JsonValue, arguments),
        ),
        ToolResultHistoryEntry(
            id=entry_id + TOOL_RESULT_SUFFIX,
            index=result_index,
            turn_id=turn_id,
            generation_status="complete" if finished else "generating",
            created_at=created_at,
            updated_at=updated_at,
            annotations={VIBE_ANNOTATION_PREFIX + "effect": display},
            tool_call_id=tool_call_id,
            name=name,
            result=(
                None
                if result_error is not None
                else cast(JsonValue, state.get("output", state.get("outputText")))
            ),
            error=result_error,
        ),
    ]


def model_tool_call_id(
    effect_entry_id: str, tool_calls: Mapping[str, JsonObject]
) -> str:
    """The model's call ID for a tool entry, else its Runtime action ID."""
    call_id = tool_calls.get(effect_entry_id, {}).get("toolCallId")
    if isinstance(call_id, str) and call_id:
        return call_id
    return effect_entry_id.removeprefix("effect-")


def _base_fields(raw: JsonObject, index: int) -> dict[str, Any]:
    created_at = _int(raw.get("createdAt"))
    annotations: dict[str, JsonValue] = {}
    for key in ("relatedEntryId", "inputEntryId"):
        if raw.get(key) is not None:
            annotations[VIBE_ANNOTATION_PREFIX + key] = cast(JsonValue, raw[key])
    return {
        "id": cast(str, raw["id"]),
        "index": index,
        "turn_id": optional_str(raw.get("turnId")),
        "generation_status": (
            "generating" if raw.get("generationStatus") == "in_progress" else "complete"
        ),
        "created_at": created_at,
        "updated_at": _int(raw.get("updatedAt"), default=created_at),
        "annotations": annotations,
    }


def _content_block(raw: object) -> ContentBlock | None:
    if not isinstance(raw, Mapping):
        return None
    match raw.get("type"):
        case "text":
            return TextContentBlock(text=optional_str(raw.get("text")) or "")
        case "image":
            return _image_block(raw)
        case "resource":
            return _resource_block(raw.get("resource"))
    return None


def _image_block(raw: Mapping[str, Any]) -> ContentBlock | None:
    attachment = raw.get("attachment")
    if not isinstance(attachment, Mapping):
        uri = optional_str(raw.get("uri"))
        return ImageContentBlock(uri=uri) if uri else None
    source = attachment.get("source")
    mime_type = optional_str(attachment.get("mimeType"))
    if not isinstance(source, Mapping):
        return None
    if source.get("kind") == "inline" and isinstance(source.get("data"), str):
        prefix = f"data:{mime_type or 'application/octet-stream'};base64,"
        return ImageContentBlock(uri=prefix + source["data"], media_type=mime_type)
    if source.get("kind") == "file" and isinstance(source.get("path"), str):
        return ImageContentBlock(uri=source["path"], media_type=mime_type)
    return None


def _resource_block(resource: object) -> ContentBlock | None:
    if not isinstance(resource, Mapping) or not isinstance(resource.get("uri"), str):
        return None
    uri = cast(str, resource["uri"])
    media_type = optional_str(resource.get("mediaType"))
    if resource.get("kind") == "link":
        size = resource.get("size")
        return ResourceLinkContentBlock(
            uri=uri,
            name=optional_str(resource.get("name")),
            title=optional_str(resource.get("title")),
            description=optional_str(resource.get("description")),
            media_type=media_type,
            size=size if isinstance(size, int) and size >= 0 else None,
        )
    text = optional_str(resource.get("text"))
    blob = optional_str(resource.get("blob"))
    if (text is None) == (blob is None):
        return None
    return EmbeddedResourceContentBlock(
        uri=uri, media_type=media_type, text=text, blob=blob
    )


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def optional_str(value: object) -> str | None:
    """``value`` if it is a string, else None."""
    return value if isinstance(value, str) else None


def _int(value: object, *, default: int = 0) -> int:
    return value if isinstance(value, int) else default


__all__ = [
    "CALLBACK_ENTRY_FIELDS",
    "ENTRY_BUILDERS",
    "TOOL_CALL_SUFFIX",
    "TOOL_RESULT_SUFFIX",
    "VIBE_ANNOTATION_PREFIX",
    "convert_history_entry",
    "model_tool_call_id",
    "optional_str",
]
