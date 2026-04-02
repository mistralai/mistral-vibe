from __future__ import annotations

import random

from rich.text import Text
from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea

FEEDBACK_PROBABILITY = 0.02
THANK_YOU_DURATION = 2.0


class FeedbackBar(Widget):
    class FeedbackGiven(Message):
        def __init__(self, rating: int) -> None:
            super().__init__()
            self.rating = rating

    @staticmethod
    def _prompt_text() -> Text:
        text = Text()
        text.append("How is Vibe doing so far?  ")
        text.append("1", style="blue")
        text.append(": good  ")
        text.append("2", style="blue")
        text.append(": fine  ")
        text.append("3", style="blue")
        text.append(": bad")
        return text

    def compose(self) -> ComposeResult:
        yield Static(self._prompt_text(), id="feedback-text")

    def on_mount(self) -> None:
        self.display = False

    def maybe_show(self) -> None:
        if self.display:
            return
        if random.random() <= FEEDBACK_PROBABILITY:
            self._set_active(True)

    def hide(self) -> None:
        if self.display:
            self._set_active(False)

    def handle_feedback_key(self, rating: int) -> None:
        try:
            self.app.query_one(ChatTextArea).feedback_active = False
        except Exception:
            pass
        self.query_one("#feedback-text", Static).update(
            Text("Thank you for your feedback!")
        )
        self.post_message(self.FeedbackGiven(rating))
        self.set_timer(THANK_YOU_DURATION, lambda: self._set_active(False))

    def _set_active(self, active: bool) -> None:
        if active:
            self.query_one("#feedback-text", Static).update(self._prompt_text())
        self.display = active
        try:
            self.app.query_one(ChatTextArea).feedback_active = active
        except Exception:
            pass
