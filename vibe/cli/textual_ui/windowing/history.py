from __future__ import annotations

from collections.abc import Sequence
from weakref import WeakKeyDictionary

from textual.widget import Widget

from vibe.app_server.models import (
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicReasoningEntry,
)
from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    ReasoningMessage,
    UserMessage,
)
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage


def history_entry_renders_widget(entry: PublicHistoryEntry) -> bool:
    match entry:
        case PublicMessageEntry(role="user"):
            return True
        case PublicMessageEntry(role="assistant"):
            return bool(entry.text)
        case PublicReasoningEntry() | PublicEffectEntry():
            return True
        case PublicCheckpointEntry(kind="compaction"):
            return True
        case _:
            return False


def build_history_widgets(
    batch: Sequence[PublicHistoryEntry],
    *,
    start_index: int,
    history_widget_indices: WeakKeyDictionary[Widget, int],
    tools_collapsed: bool,
) -> list[Widget]:
    widgets: list[Widget] = []

    for history_index, entry in zip(
        range(start_index, start_index + len(batch)), batch, strict=True
    ):
        entry_widgets = _entry_widgets(entry, history_index, tools_collapsed)
        widgets.extend(entry_widgets)
        for widget in entry_widgets:
            history_widget_indices[widget] = history_index

    return widgets


def _entry_widgets(
    entry: PublicHistoryEntry, history_index: int, tools_collapsed: bool
) -> list[Widget]:
    match entry:
        case PublicMessageEntry(role="user"):
            return [
                UserMessage(
                    entry.text, history_entry_id=entry.id, images=entry.images or None
                )
            ]
        case PublicMessageEntry(role="assistant"):
            return [AssistantMessage(entry.text)] if entry.text else []
        case PublicReasoningEntry():
            return [
                ReasoningMessage(
                    entry.text,
                    collapsed=tools_collapsed,
                    completed=(
                        entry.generation_status is PublicEntryGenerationStatus.COMPLETED
                    ),
                )
            ]
        case PublicEffectEntry():
            call = ToolCallMessage(entry)
            return [call, ToolResultMessage(entry, call)]
        case PublicCheckpointEntry(kind="compaction"):
            message = CompactMessage()
            message.set_complete()
            return [message]
        case _:
            return []


def split_history_tail(
    history: list[PublicHistoryEntry], tail_size: int
) -> tuple[list[PublicHistoryEntry], list[PublicHistoryEntry], int]:
    tail = history[-tail_size:]
    backfill = history[:-tail_size]
    return tail, backfill, len(history) - len(tail)


def visible_history_indices(
    children: list[Widget], history_widget_indices: WeakKeyDictionary[Widget, int]
) -> list[int]:
    return [
        index
        for child in children
        if (index := history_widget_indices.get(child)) is not None
    ]


def visible_history_widgets_count(children: list[Widget]) -> int:
    history_widget_types = (
        UserMessage,
        AssistantMessage,
        CompactMessage,
        ReasoningMessage,
        ToolCallMessage,
        ToolResultMessage,
    )
    return sum(isinstance(child, history_widget_types) for child in children)


def shift_history_widget_indices(
    history_widget_indices: WeakKeyDictionary[Widget, int], offset: int
) -> None:
    for widget, index in list(history_widget_indices.items()):
        history_widget_indices[widget] = index + offset
