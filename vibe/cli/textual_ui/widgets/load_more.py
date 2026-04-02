from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button, Static


class HistoryLoadMoreRequested(Message):
    pass


class HistoryLoadMoreMessage(Static):
    def __init__(self) -> None:
        super().__init__()
        self.add_class("history-load-more-message")
        self._label_widget: Button | None = None
        self._remaining: int | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="history-load-more-container"):
            self._label_widget = Button(
                self._label_text(), classes="history-load-more-button"
            )
            yield self._label_widget

    def _label_text(self) -> str:
        if self._remaining is None:
            return "Load more messages"
        return f"Load more messages ({self._remaining})"

    def set_enabled(self, enabled: bool) -> None:
        if self._label_widget:
            self._label_widget.disabled = not enabled

    def set_remaining(self, remaining: int | None) -> None:
        self._remaining = remaining
        if self._label_widget:
            self._label_widget.label = self._label_text()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(HistoryLoadMoreRequested())
