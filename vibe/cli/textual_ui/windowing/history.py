from __future__ import annotations

from collections.abc import Callable, Sequence
from weakref import WeakKeyDictionary

from textual.widget import Widget

from vibe.app_server.models import (
    CancelledEffectState,
    CompletedEffectState,
    FailedEffectState,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicReasoningEntry,
    SkippedEffectState,
)
from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    ReasoningMessage,
    UserMessage,
)
from vibe.cli.textual_ui.widgets.tools import (
    ToolCallMessage,
    ToolGroup,
    ToolResultMessage,
    _effect_state_is_failure,
    _effect_state_to_indicator,
    entry_keeps_tool_group,
)


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
    show_thinking: bool = True,
) -> list[Widget]:
    widgets: list[Widget] = []
    current_group: ToolGroup | None = None
    pending_error_results: list[ToolResultMessage] = []

    def _resolve_pending_errors(*, escalate: bool) -> None:
        if escalate:
            for result in pending_error_results:
                result._should_escalate = True
        pending_error_results.clear()

    for history_index, entry in zip(
        range(start_index, start_index + len(batch)), batch, strict=True
    ):
        if not show_thinking and isinstance(entry, PublicReasoningEntry):
            continue
        if current_group is not None and not entry_keeps_tool_group(entry):
            _resolve_pending_errors(escalate=True)
            current_group.set_collapsed(tools_collapsed)
            current_group.finalize()
            widgets.append(current_group)
            current_group = None

        if entry_keeps_tool_group(entry):
            entry_widgets = _entry_widgets(
                entry, history_index, tools_collapsed, show_thinking=show_thinking
            )
            if current_group is None:
                current_group = ToolGroup()
            group = current_group
            group.set_collapsed(tools_collapsed)
            for w in entry_widgets:
                group.add_content_child(w)
                history_widget_indices[w] = history_index
            if isinstance(entry, PublicEffectEntry):
                group.add_call_kind(entry.detail.kind)
                _handle_terminal_effect(
                    entry,
                    entry_widgets,
                    group,
                    pending_error_results,
                    _resolve_pending_errors,
                )
            elif isinstance(entry, PublicReasoningEntry) and show_thinking:
                group.mark_reasoning()
        else:
            entry_widgets = _entry_widgets(
                entry, history_index, tools_collapsed, show_thinking=show_thinking
            )
            widgets.extend(entry_widgets)
            for widget in entry_widgets:
                history_widget_indices[widget] = history_index

    if current_group is not None:
        _resolve_pending_errors(escalate=True)
        current_group.set_collapsed(tools_collapsed)
        current_group.finalize()
        widgets.append(current_group)

    return widgets


def _handle_terminal_effect(
    entry: PublicEffectEntry,
    entry_widgets: list[Widget],
    group: ToolGroup,
    pending_error_results: list[ToolResultMessage],
    resolve_pending_errors: Callable[..., None],
) -> None:
    if not _is_terminal_effect(entry):
        return
    group.settle_indicator(_effect_state_to_indicator(entry.state))
    if _effect_state_is_failure(entry.state):
        result = _find_result_widget(entry_widgets)
        if result is not None:
            pending_error_results.append(result)
    elif isinstance(entry.state, CompletedEffectState):
        resolve_pending_errors(escalate=False)


def _find_result_widget(widgets: list[Widget]) -> ToolResultMessage | None:
    for w in widgets:
        if isinstance(w, ToolResultMessage):
            return w
    return None


def _is_terminal_effect(entry: PublicEffectEntry) -> bool:
    return isinstance(
        entry.state,
        CompletedEffectState
        | FailedEffectState
        | CancelledEffectState
        | SkippedEffectState,
    )


def _entry_widgets(  # noqa: PLR0911
    entry: PublicHistoryEntry,
    history_index: int,
    tools_collapsed: bool,
    *,
    show_thinking: bool = True,
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
            if not show_thinking:
                return []
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
    indices: list[int] = []
    for child in children:
        if isinstance(child, ToolGroup):
            for gc in child.content_container.children:
                if (index := history_widget_indices.get(gc)) is not None:
                    indices.append(index)
        elif (index := history_widget_indices.get(child)) is not None:
            indices.append(index)
    return indices


def visible_history_widgets_count(children: list[Widget]) -> int:
    history_widget_types = (
        UserMessage,
        AssistantMessage,
        CompactMessage,
        ReasoningMessage,
        ToolCallMessage,
        ToolResultMessage,
        ToolGroup,
    )
    return sum(isinstance(child, history_widget_types) for child in children)


def shift_history_widget_indices(
    history_widget_indices: WeakKeyDictionary[Widget, int], offset: int
) -> None:
    for widget, index in list(history_widget_indices.items()):
        history_widget_indices[widget] = index + offset
