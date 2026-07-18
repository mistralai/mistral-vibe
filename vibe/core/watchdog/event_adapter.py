from __future__ import annotations

from dataclasses import dataclass
import time

from pydantic import JsonValue

from vibe.core.types import (
    AssistantEvent,
    BaseEvent,
    CompactEndEvent,
    CompactStartEvent,
    ReasoningEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    WaitingForInputEvent,
)
from vibe.core.watchdog.events import EventKind


@dataclass(frozen=True, slots=True)
class PendingWatchdogEvent:
    kind: EventKind
    observed_at_monotonic: float
    payload: dict[str, JsonValue]
    critical: bool
    coalesce_key: str | None = None


def normalize_event(event: BaseEvent) -> PendingWatchdogEvent | None:
    observed_at = time.monotonic()
    match event:
        case AssistantEvent() | ReasoningEvent():
            return _normalize_model_event(event, observed_at)
        case ToolCallEvent() | ToolStreamEvent() | ToolResultEvent():
            return _normalize_tool_event(event, observed_at)
        case WaitingForInputEvent() | CompactStartEvent() | CompactEndEvent():
            return _normalize_boundary_event(event, observed_at)
        case _:
            return None


def _normalize_model_event(
    event: AssistantEvent | ReasoningEvent, observed_at: float
) -> PendingWatchdogEvent:
    return PendingWatchdogEvent(
        kind=EventKind.MODEL_ACTIVITY,
        observed_at_monotonic=observed_at,
        payload={"message_id": event.message_id},
        critical=False,
        coalesce_key="model",
    )


def _normalize_tool_event(
    event: ToolCallEvent | ToolStreamEvent | ToolResultEvent, observed_at: float
) -> PendingWatchdogEvent:
    match event:
        case ToolCallEvent():
            arguments = event.args.model_dump(mode="json") if event.args else None
            return PendingWatchdogEvent(
                kind=EventKind.TOOL_STARTED,
                observed_at_monotonic=observed_at,
                payload={
                    "tool_call_id": event.tool_call_id,
                    "tool_name": event.tool_name,
                    "arguments": arguments,
                },
                critical=True,
            )
        case ToolStreamEvent():
            return PendingWatchdogEvent(
                kind=EventKind.TOOL_PROGRESS,
                observed_at_monotonic=observed_at,
                payload={
                    "tool_call_id": event.tool_call_id,
                    "tool_name": event.tool_name,
                    "message_length": len(event.message),
                },
                critical=False,
                coalesce_key=f"tool:{event.tool_call_id}",
            )
        case ToolResultEvent():
            result = event.result.model_dump(mode="json") if event.result else None
            return PendingWatchdogEvent(
                kind=EventKind.TOOL_FINISHED,
                observed_at_monotonic=observed_at,
                payload={
                    "tool_call_id": event.tool_call_id,
                    "tool_name": event.tool_name,
                    "result": result,
                    "error": event.error,
                    "cancelled": event.cancelled,
                    "skipped": event.skipped,
                },
                critical=True,
            )


def _normalize_boundary_event(
    event: WaitingForInputEvent | CompactStartEvent | CompactEndEvent,
    observed_at: float,
) -> PendingWatchdogEvent:
    match event:
        case WaitingForInputEvent():
            return PendingWatchdogEvent(
                kind=EventKind.WAITING_FOR_USER,
                observed_at_monotonic=observed_at,
                payload={"task_id": event.task_id, "label": event.label},
                critical=True,
            )
        case CompactStartEvent():
            return PendingWatchdogEvent(
                kind=EventKind.COMPACTION_STARTED,
                observed_at_monotonic=observed_at,
                payload={
                    "tool_call_id": event.tool_call_id,
                    "current_context_tokens": event.current_context_tokens,
                    "threshold": event.threshold,
                },
                critical=True,
            )
        case CompactEndEvent():
            return PendingWatchdogEvent(
                kind=EventKind.COMPACTION_FINISHED,
                observed_at_monotonic=observed_at,
                payload={
                    "tool_call_id": event.tool_call_id,
                    "summary_length": event.summary_length,
                },
                critical=True,
            )
