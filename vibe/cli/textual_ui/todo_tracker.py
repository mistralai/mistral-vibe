from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from pydantic import ValidationError

from vibe.app_server.models import (
    CompletedEffectState,
    PublicEffectEntry,
    PublicHistoryEntry,
    TodoEffectItem,
    TodoEffectOutput,
    TodoEffectStatus,
)
from vibe.utils.tool_presentation import ToolEffectKind

_ACTIVE_ICON = "▶"
_PENDING_ICON = "☐"
_DONE_ICON = "☑"
_CANCELLED_ICON = "☒"

_STATUS_ICONS: dict[TodoEffectStatus, str] = {
    TodoEffectStatus.PENDING: _PENDING_ICON,
    TodoEffectStatus.IN_PROGRESS: _ACTIVE_ICON,
    TodoEffectStatus.COMPLETED: _DONE_ICON,
    TodoEffectStatus.CANCELLED: _CANCELLED_ICON,
}

_TRANSITION_VERBS: dict[TodoEffectStatus, str] = {
    TodoEffectStatus.IN_PROGRESS: "started",
    TodoEffectStatus.COMPLETED: "completed",
    TodoEffectStatus.CANCELLED: "cancelled",
    TodoEffectStatus.PENDING: "reopened",
}

_TRANSITION_ORDER = (
    TodoEffectStatus.IN_PROGRESS,
    TodoEffectStatus.COMPLETED,
    TodoEffectStatus.CANCELLED,
    TodoEffectStatus.PENDING,
)


def status_icon(status: TodoEffectStatus) -> str:
    return _STATUS_ICONS.get(status, _PENDING_ICON)


def progress_label(todos: list[TodoEffectItem]) -> str:
    # Cancelled items leave the denominator, so an abandoned step reads 4/4, not 4/5.
    countable = [t for t in todos if t.status is not TodoEffectStatus.CANCELLED]
    done = sum(1 for t in countable if t.status is TodoEffectStatus.COMPLETED)
    return f"{done}/{len(countable)}"


def summary_line(todos: list[TodoEffectItem]) -> str | None:
    if not todos:
        return None
    progress = progress_label(todos)
    focus = _focus_item(todos)
    if focus is not None:
        return f"{status_icon(focus.status)} {progress} · {focus.content}"
    # Cancelled items are out of the denominator, so a list that is nothing but
    # cancellations has neither a focus nor anything completed to claim.
    if all(todo.status is TodoEffectStatus.CANCELLED for todo in todos):
        return f"{_CANCELLED_ICON} {progress} · All todos cancelled"
    return f"{_DONE_ICON} {progress} · All todos complete"


def _focus_item(todos: list[TodoEffectItem]) -> TodoEffectItem | None:
    for todo in todos:
        if todo.status is TodoEffectStatus.IN_PROGRESS:
            return todo
    for todo in todos:
        if todo.status is TodoEffectStatus.PENDING:
            return todo
    return None


def summarize_change(
    previous: list[TodoEffectItem], current: list[TodoEffectItem]
) -> str:
    if not current:
        return "Cleared todos" if previous else "No todos"

    previous_by_id = {todo.id: todo for todo in previous}
    current_ids = {todo.id for todo in current}
    added = sum(1 for todo in current if todo.id not in previous_by_id)
    removed = sum(1 for todo in previous if todo.id not in current_ids)
    transitions = Counter(
        todo.status
        for todo in current
        if todo.id in previous_by_id
        and previous_by_id[todo.id].status is not todo.status
    )

    parts: list[str] = []
    if added:
        parts.append(f"+{added}")
    if removed:
        parts.append(f"−{removed}")
    parts.extend(
        f"{transitions[status]} {_TRANSITION_VERBS[status]}"
        for status in _TRANSITION_ORDER
        if transitions[status]
    )
    if not parts:
        parts.append("no changes")
    return f"{' · '.join(parts)} · {progress_label(current)} done"


class TodoTracker:
    def __init__(self) -> None:
        self._todos: list[TodoEffectItem] = []

    @property
    def todos(self) -> list[TodoEffectItem]:
        return list(self._todos)

    @property
    def summary(self) -> str | None:
        return summary_line(self._todos)

    def record(self, todos: list[TodoEffectItem]) -> str:
        delta = summarize_change(self._todos, todos)
        self._todos = list(todos)
        return delta

    def seed(self, todos: list[TodoEffectItem]) -> None:
        self._todos = list(todos)


def _entry_todos(entry: PublicHistoryEntry) -> list[TodoEffectItem] | None:
    if (
        not isinstance(entry, PublicEffectEntry)
        or entry.detail.kind is not ToolEffectKind.TODO
        or not isinstance(entry.state, CompletedEffectState)
    ):
        return None
    try:
        return TodoEffectOutput.model_validate(entry.state.output).todos
    except ValidationError:
        return None


def todo_deltas(history: Iterable[PublicHistoryEntry]) -> dict[str, str]:
    """Replay the whole history to recover each todo entry's delta, keyed by entry id.

    Deltas are relative to the preceding write, so a re-windowed page cannot derive
    its own: without this the same result renders as a one-line delta while it
    streams and as a full list reprint once it is rebuilt from history.
    """
    tracker = TodoTracker()
    deltas: dict[str, str] = {}
    for entry in history:
        todos = _entry_todos(entry)
        if todos is not None:
            deltas[entry.id] = tracker.record(todos)
    return deltas
