from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Markdown

from vibe.cli.textual_ui.widgets.messages import ExpandingBorder, StreamingMessageBase


class BtwMessage(StreamingMessageBase):
    def __init__(self, content: str = "") -> None:
        super().__init__(content)
        self.add_class("btw-message")
        self._error_message: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="btw-container"):
            yield ExpandingBorder(classes="btw-border")
            with Vertical(classes="btw-content"):
                markdown = Markdown("")
                self._markdown = markdown
                yield markdown

    def set_error(self, error_message: str) -> None:
        self._error_message = error_message
        self._content = f"Error: {error_message}"
        if self._markdown is not None:
            self._markdown.update(self._content)
