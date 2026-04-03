from __future__ import annotations

from enum import Enum, auto
import re

from vibe.core.types import BaseEvent, ToolResultEvent

CANCELLATION_TAG = "user_cancellation"
TOOL_ERROR_TAG = "tool_error"
VIBE_STOP_EVENT_TAG = "vibe_stop_event"
VIBE_WARNING_TAG = "vibe_warning"

KNOWN_TAGS = [CANCELLATION_TAG, TOOL_ERROR_TAG, VIBE_STOP_EVENT_TAG, VIBE_WARNING_TAG]


class TaggedText:
    _TAG_PATTERN = re.compile(
        rf"<({'|'.join(re.escape(tag) for tag in KNOWN_TAGS)})>(.*?)</\1>",
        flags=re.DOTALL,
    )

    def __init__(self, message: str, tag: str = "") -> None:
        self.message = message
        self.tag = tag

    def __str__(self) -> str:
        if not self.tag:
            return self.message
        return f"<{self.tag}>{self.message}</{self.tag}>"

    @staticmethod
    def from_string(text: str) -> TaggedText:
        found_tag = ""
        result = text

        def replace_tag(match: re.Match[str]) -> str:
            nonlocal found_tag
            tag_name = match.group(1)
            content = match.group(2)
            if not found_tag:
                found_tag = tag_name
            return content

        result = TaggedText._TAG_PATTERN.sub(replace_tag, text)

        if found_tag:
            return TaggedText(result, found_tag)

        return TaggedText(text, "")


class CancellationReason(Enum):
    OPERATION_CANCELLED = auto()
    TOOL_INTERRUPTED = auto()
    TOOL_NO_RESPONSE = auto()
    TOOL_SKIPPED = auto()


def get_user_cancellation_message(
    cancellation_reason: CancellationReason, tool_name: str | None = None
) -> TaggedText:
    match cancellation_reason:
        case CancellationReason.OPERATION_CANCELLED:
            return TaggedText("User cancelled the operation.", CANCELLATION_TAG)
        case CancellationReason.TOOL_INTERRUPTED:
            return TaggedText("Tool execution interrupted by user.", CANCELLATION_TAG)
        case CancellationReason.TOOL_NO_RESPONSE:
            return TaggedText(
                "Tool execution interrupted - no response available", CANCELLATION_TAG
            )
        case CancellationReason.TOOL_SKIPPED:
            return TaggedText(
                tool_name or "Tool execution skipped by user.", CANCELLATION_TAG
            )


def is_user_cancellation_event(event: BaseEvent) -> bool:
    if not isinstance(event, ToolResultEvent):
        return False
    return event.cancelled
