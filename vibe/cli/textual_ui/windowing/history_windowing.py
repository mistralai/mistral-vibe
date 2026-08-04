from __future__ import annotations

from dataclasses import dataclass
from weakref import WeakKeyDictionary

from textual.widget import Widget

from vibe.app_server.models import PublicHistoryEntry
from vibe.cli.textual_ui.windowing.history import (
    split_history_tail,
    visible_history_indices,
    visible_history_widgets_count,
)
from vibe.cli.textual_ui.windowing.state import SessionWindowing


@dataclass(frozen=True)
class HistoryResumePlan:
    tail_entries: list[PublicHistoryEntry]
    backfill_entries: list[PublicHistoryEntry]
    tail_start_index: int

    @property
    def has_backfill(self) -> bool:
        return bool(self.backfill_entries)


def should_resume_history(messages_children: list[Widget]) -> bool:
    return visible_history_widgets_count(messages_children) == 0


def create_resume_plan(
    history: list[PublicHistoryEntry], tail_size: int
) -> HistoryResumePlan | None:
    if not history:
        return None
    tail, backfill, tail_start_index = split_history_tail(history, tail_size)
    return HistoryResumePlan(
        tail_entries=tail, backfill_entries=backfill, tail_start_index=tail_start_index
    )


def sync_backfill_state(
    *,
    history: list[PublicHistoryEntry],
    messages_children: list[Widget],
    history_widget_indices: WeakKeyDictionary[Widget, int],
    windowing: SessionWindowing,
) -> bool:
    if not history:
        windowing.reset()
        return False
    visible_indices = visible_history_indices(messages_children, history_widget_indices)
    visible_widgets = visible_history_widgets_count(messages_children)
    return windowing.recompute_backfill(
        history,
        visible_indices=visible_indices,
        visible_history_widgets_count=visible_widgets,
    )
