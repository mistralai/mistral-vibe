from __future__ import annotations

from typing import Any, ClassVar, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.config._settings import ThinkingLevel


def _build_option_text(level: str, is_current: bool) -> Text:
    text = Text(no_wrap=True)
    marker = "› " if is_current else "  "
    style = "bold" if is_current else ""
    text.append(marker, style="green" if is_current else "")
    text.append(level.capitalize(), style=style)
    return text


class ThinkingPickerApp(Container):
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False)
    ]

    class ThinkingSelected(Message):
        level: ThinkingLevel

        def __init__(self, level: ThinkingLevel) -> None:
            self.level = level
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(
        self, thinking_levels: list[str], current_thinking: str, **kwargs: Any
    ) -> None:
        super().__init__(id="thinkingpicker-app", **kwargs)
        self._thinking_levels = thinking_levels
        self._current_thinking = current_thinking

    def compose(self) -> ComposeResult:
        options = [
            Option(_build_option_text(level, level == self._current_thinking), id=level)
            for level in self._thinking_levels
        ]
        with Vertical(id="thinkingpicker-content"):
            yield NoMarkupStatic(
                "Select Thinking Level", classes="thinkingpicker-title"
            )
            yield OptionList(*options, id="thinkingpicker-options")
            yield NoMarkupStatic(
                "↑↓ Navigate  Enter Select  Esc Cancel", classes="thinkingpicker-help"
            )

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        for i, level in enumerate(self._thinking_levels):
            if level == self._current_thinking:
                option_list.highlighted = i
                break
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.post_message(
                self.ThinkingSelected(cast(ThinkingLevel, event.option.id))
            )

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())
