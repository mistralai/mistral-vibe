from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Vertical
from textual.timer import Timer
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.setup.onboarding.base import OnboardingScreen

WELCOME_PREFIX = "Welcome to "
WELCOME_HIGHLIGHT = "Mistral Vibe"
WELCOME_SUFFIX = " - Let's get you started!"
WELCOME_TEXT = WELCOME_PREFIX + WELCOME_HIGHLIGHT + WELCOME_SUFFIX

HIGHLIGHT_START = len(WELCOME_PREFIX)
HIGHLIGHT_END = HIGHLIGHT_START + len(WELCOME_HIGHLIGHT)

BUTTON_TEXT = "Press Enter ↵"

GRADIENT_COLORS = [
    "#ff6b00",
    "#ff7b00",
    "#ff8c00",
    "#ff9d00",
    "#ffae00",
    "#ffbf00",
    "#ffae00",
    "#ff9d00",
    "#ff8c00",
    "#ff7b00",
]


def _apply_gradient(text: str, offset: int) -> str:
    result = []
    for i, char in enumerate(text):
        color = GRADIENT_COLORS[(i + offset) % len(GRADIENT_COLORS)]
        result.append(f"[bold {color}]{char}[/]")
    return "".join(result)


class WelcomeScreen(OnboardingScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "next", "Next", show=False, priority=True),
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    NEXT_SCREEN = "api_key"

    def __init__(self) -> None:
        super().__init__()
        self._char_index = 0
        self._gradient_offset = 0
        self._typing_done = False
        self._paused = False
        self._typing_timer: Timer | None = None
        self._button_char_index = 0
        self._button_typing_timer: Timer | None = None
        self._welcome_text: Static
        self._enter_hint: Static

    def compose(self) -> ComposeResult:
        with Vertical(id="welcome-container"):
            with Center():
                yield Static("", id="welcome-text")
            with Center():
                yield NoMarkupStatic("", id="enter-hint", classes="hidden")

    def on_mount(self) -> None:
        self._welcome_text = self.query_one("#welcome-text", Static)
        self._enter_hint = self.query_one("#enter-hint", Static)
        self._typing_timer = self.set_interval(0.04, self._type_next_char)
        self.focus()

    def _render_text(self, length: int) -> str:
        text = WELCOME_TEXT[:length]

        if length <= HIGHLIGHT_START:
            return text

        prefix = text[:HIGHLIGHT_START]
        highlight_len = min(length, HIGHLIGHT_END) - HIGHLIGHT_START
        highlight = _apply_gradient(
            WELCOME_HIGHLIGHT[:highlight_len], self._gradient_offset
        )

        if length > HIGHLIGHT_END:
            suffix = text[HIGHLIGHT_END:]
            return prefix + highlight + suffix
        return prefix + highlight

    def _type_next_char(self) -> None:
        if self._char_index >= len(WELCOME_TEXT):
            if not self._typing_done:
                self._typing_done = True
                self.set_timer(0.5, self._show_button)
            return

        if self._char_index == HIGHLIGHT_END and not self._paused:
            self._paused = True
            if self._typing_timer:
                self._typing_timer.stop()
            self.set_interval(0.08, self._animate_gradient)
            self.set_timer(1.4, self._resume_typing)
            return

        self._char_index += 1
        self._welcome_text.update(self._render_text(self._char_index))

    def _resume_typing(self) -> None:
        self._typing_timer = self.set_interval(0.03, self._type_next_char)

    def _show_button(self) -> None:
        self._enter_hint.remove_class("hidden")
        self._button_typing_timer = self.set_interval(0.03, self._type_button_char)

    def _type_button_char(self) -> None:
        if self._button_char_index >= len(BUTTON_TEXT):
            if self._button_typing_timer:
                self._button_typing_timer.stop()
            return
        self._button_char_index += 1
        self._enter_hint.update(BUTTON_TEXT[: self._button_char_index])

    def _animate_gradient(self) -> None:
        self._gradient_offset = (self._gradient_offset + 1) % len(GRADIENT_COLORS)
        self._welcome_text.update(self._render_text(self._char_index))

    def action_next(self) -> None:
        if self._typing_done:
            super().action_next()
