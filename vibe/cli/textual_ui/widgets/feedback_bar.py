from __future__ import annotations

from humanize import naturaldelta
from rich.text import Text
from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.chat_input.text_area import (
    FEEDBACK_RATING_KEYS,
    FEEDBACK_SNOOZE_KEY,
    FEEDBACK_SNOOZE_LABEL,
    ChatTextArea,
)
from vibe.core.feedback import FEEDBACK_SNOOZED_COOLDOWN_SECONDS

THANK_YOU_DURATION = 2.0


class FeedbackBar(Widget):
    class FeedbackGiven(Message):
        def __init__(self, rating: int) -> None:
            super().__init__()
            self.rating = rating

    class SnoozeKeyPressed(Message):
        pass

    @staticmethod
    def _prompt_text() -> Text:
        text = Text()
        text.append("How is Vibe doing so far?  ")
        for key, label in FEEDBACK_RATING_KEYS.items():
            text.append(key, style="blue")
            text.append(f": {label}  ")
        text.append(FEEDBACK_SNOOZE_KEY, style="blue")
        text.append(f": {FEEDBACK_SNOOZE_LABEL}")
        return text

    def compose(self) -> ComposeResult:
        yield Static(self._prompt_text(), id="feedback-text")

    def on_mount(self) -> None:
        self.display = False

    def show(self) -> None:
        if not self.display:
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

    def handle_snooze_key(self) -> None:
        try:
            self.app.query_one(ChatTextArea).feedback_active = False
        except Exception:
            pass
        snooze_duration = naturaldelta(FEEDBACK_SNOOZED_COOLDOWN_SECONDS)
        self.query_one("#feedback-text", Static).update(
            Text(f"Snoozed for {snooze_duration}. See you later!")
        )
        self.post_message(self.SnoozeKeyPressed())
        self.set_timer(THANK_YOU_DURATION, lambda: self._set_active(False))

    def _set_active(self, active: bool) -> None:
        if active:
            self.query_one("#feedback-text", Static).update(self._prompt_text())
        self.display = active
        try:
            self.app.query_one(ChatTextArea).feedback_active = active
        except Exception:
            pass
