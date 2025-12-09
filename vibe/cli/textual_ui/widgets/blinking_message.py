from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static


class BlinkingMessage(Static):
    def __init__(self, initial_text: str = "", **kwargs: Any) -> None:
        self.blink_state = False
        self._blink_timer = None
        self._is_blinking = True
        self.success = True
        self._initial_text = initial_text
        self._dot_widget: Static | None = None
        self._text_widget: Static | None = None
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with Horizontal():
            self._dot_widget = Static("● ", classes="blink-dot")
            yield self._dot_widget
            self._text_widget = Static("", markup=False, classes="blink-text")
            yield self._text_widget

    def on_mount(self) -> None:
        self.update_display()
        self._blink_timer = self.set_interval(0.5, self.toggle_blink)

    def toggle_blink(self) -> None:
        if not self._is_blinking:
            return
        self.blink_state = not self.blink_state
        self.update_display()

    def update_display(self) -> None:
        if not self._dot_widget or not self._text_widget:
            return

        content = self.get_content()

        if self._is_blinking:
            dot = "● " if self.blink_state else "○ "
            self._dot_widget.update(dot)
            self._dot_widget.remove_class("success")
            self._dot_widget.remove_class("error")
        else:
            self._dot_widget.update("● ")
            if self.success:
                self._dot_widget.add_class("success")
                self._dot_widget.remove_class("error")
            else:
                self._dot_widget.add_class("error")
                self._dot_widget.remove_class("success")

        self._text_widget.update(content)

    def get_content(self) -> str:
        return self._initial_text

    def stop_blinking(self, success: bool = True) -> None:
        self._is_blinking = False
        self.blink_state = True
        self.success = success
        self.update_display()
