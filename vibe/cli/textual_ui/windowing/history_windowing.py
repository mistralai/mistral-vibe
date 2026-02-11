from __future__ import annotations

from dataclasses import dataclass
from weakref import WeakKeyDictionary

from textual.widget import Widget

from vibe.cli.textual_ui.widgets.messages import WhatsNewMessage
from vibe.cli.textual_ui.windowing.history import (
    build_tool_call_map,
    split_history_tail,
    visible_history_indices,
    visible_history_widgets_count,
)
from vibe.cli.textual_ui.windowing.state import SessionWindowing
from vibe.core.types import LLMMessage


@dataclass(frozen=True)
class HistoryResumePlan:
    tool_call_map: dict[str, str]
    tail_messages: list[LLMMessage]
    backfill_messages: list[LLMMessage]
    tail_start_index: int

    @property
    def has_backfill(self) -> bool:
        return bool(self.backfill_messages)


def should_resume_history(messages_children: list[Widget]) -> bool:
    allowed_pre_existing_types = (WhatsNewMessage,)
    return all(
        isinstance(child, allowed_pre_existing_types) for child in messages_children
    )


def create_resume_plan(
    history_messages: list[LLMMessage], tail_size: int
) -> HistoryResumePlan | None:
    if not history_messages:
        return None
    tail_messages, backfill_messages, tail_start_index = split_history_tail(
        history_messages, tail_size
    )
    return HistoryResumePlan(
        tool_call_map=build_tool_call_map(history_messages),
        tail_messages=tail_messages,
        backfill_messages=backfill_messages,
        tail_start_index=tail_start_index,
    )


def sync_backfill_state(
    *,
    history_messages: list[LLMMessage],
    messages_children: list[Widget],
    history_widget_indices: WeakKeyDictionary[Widget, int],
    windowing: SessionWindowing,
) -> tuple[bool, dict[str, str] | None]:
    if not history_messages:
        windowing.reset()
        return False, None
    visible_indices = visible_history_indices(messages_children, history_widget_indices)
    visible_history_widgets = visible_history_widgets_count(messages_children)
    has_backfill = windowing.recompute_backfill(
        history_messages,
        visible_indices=visible_indices,
        visible_history_widgets_count=visible_history_widgets,
    )
    return has_backfill, build_tool_call_map(history_messages)
