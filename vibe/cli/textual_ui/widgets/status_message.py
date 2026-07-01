from __future__ import annotations

from enum import StrEnum, auto
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.no_markup_static import (
    NoMarkupStatic,
    NonSelectableStatic,
)
from vibe.cli.textual_ui.widgets.spinner import SpinnerMixin, SpinnerType


class IndicatorState(StrEnum):
    SUCCESS = auto()
    ERROR = auto()
    MUTED = auto()

    @property
    def glyph(self) -> str:
        match self:
            case IndicatorState.SUCCESS:
                return "✓"
            case IndicatorState.ERROR:
                return "✕"
            case IndicatorState.MUTED:
                return "□"

    @property
    def css_class(self) -> str:
        return self.value


class StatusMessage(SpinnerMixin, NoMarkupStatic):
    SPINNER_TYPE: ClassVar[SpinnerType] = SpinnerType.PULSE

    def __init__(self, initial_text: str = "", **kwargs: Any) -> None:
        self._initial_text = initial_text
        self._indicator_widget: Static | None = None
        self._text_widget: Static | None = None
        self._state = IndicatorState.SUCCESS
        self.init_spinner()
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Horizontal():
            self._indicator_widget = NonSelectableStatic(
                self._spinner.current_frame(), classes="status-indicator-icon"
            )
            yield self._indicator_widget
            self._text_widget = NoMarkupStatic("", classes="status-indicator-text")
            yield self._text_widget

    def on_mount(self) -> None:
        self.update_display()
        self.start_spinner_timer()

    def on_resize(self) -> None:
        self.refresh_spinner()

    def _update_spinner_frame(self) -> None:
        if not self._is_spinning:
            return
        self.update_display()

    def update_display(self) -> None:
        if not self._indicator_widget or not self._text_widget:
            return

        if self._is_spinning:
            self._indicator_widget.update(self._spinner.next_frame())
        else:
            self._indicator_widget.update(self._state.glyph)

        for state in IndicatorState:
            self._indicator_widget.set_class(
                not self._is_spinning and state is self._state, state.css_class
            )

        self._text_widget.update(self._format_text(self.get_content()))

    def _format_text(self, content: str) -> str:
        return content

    def get_content(self) -> str:
        return self._initial_text

    def stop_spinning(self, success: bool = True) -> None:
        self.settle(IndicatorState.SUCCESS if success else IndicatorState.ERROR)

    def settle(self, state: IndicatorState) -> None:
        self._is_spinning = False
        self._state = state
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self.update_display()
