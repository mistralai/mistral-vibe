from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen

from vibe.app_server.models import TodoEffectItem, TodoEffectStatus
from vibe.cli.textual_ui.todo_tracker import progress_label, status_icon
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

TODO_OVERLAY_SCREEN_ID = "todo-overlay-screen"

_STATUS_ORDER = (
    TodoEffectStatus.IN_PROGRESS,
    TodoEffectStatus.PENDING,
    TodoEffectStatus.COMPLETED,
    TodoEffectStatus.CANCELLED,
)


class TodoOverlayScreen(ModalScreen[None]):
    SCOPED_CSS = False
    CSS_PATH = "todo_overlay.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_overlay", "Close", show=False),
        Binding("q", "dismiss_overlay", "Close", show=False),
    ]

    def __init__(self, todos: list[TodoEffectItem]) -> None:
        super().__init__(id=TODO_OVERLAY_SCREEN_ID)
        self._todos = todos

    def compose(self) -> ComposeResult:
        with Vertical(id="todo-overlay-content") as content:
            content.border_title = f"Todos · {progress_label(self._todos)} done"
            with VerticalScroll(id="todo-overlay-list"):
                yield from self._compose_items()
            yield NoMarkupStatic("esc to close", id="todo-overlay-hint")

    def _compose_items(self) -> ComposeResult:
        if not self._todos:
            yield NoMarkupStatic("No todos", classes="todo-empty")
            return
        for status in _STATUS_ORDER:
            for todo in self._todos:
                if todo.status is not status:
                    continue
                yield NoMarkupStatic(
                    f"{status_icon(status)} {todo.content}",
                    classes=f"todo-{status.value}",
                )

    def action_dismiss_overlay(self) -> None:
        self.dismiss(None)
