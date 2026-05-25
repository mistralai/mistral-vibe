from __future__ import annotations

from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.theme import BUILTIN_THEMES
from textual.timer import Timer
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

PREVIEW_DEBOUNCE_SECONDS = 0.1


def sorted_theme_names() -> list[str]:
    light = sorted(name for name, t in BUILTIN_THEMES.items() if not t.dark)
    dark = sorted(name for name, t in BUILTIN_THEMES.items() if t.dark)
    return light + dark


def _build_option_text(theme: str, is_current: bool) -> Text:
    text = Text(no_wrap=True)
    marker = "› " if is_current else "  "
    text.append(marker, style="green" if is_current else "")
    text.append(theme, style="bold" if is_current else "")
    return text


class ThemePickerApp(Container):
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False)
    ]

    class ThemeSelected(Message):
        def __init__(self, theme: str) -> None:
            self.theme = theme
            super().__init__()

    class ThemePreviewed(Message):
        def __init__(self, theme: str) -> None:
            self.theme = theme
            super().__init__()

    class Cancelled(Message):
        def __init__(self, original_theme: str) -> None:
            self.original_theme = original_theme
            super().__init__()

    def __init__(
        self, theme_names: list[str], current_theme: str, **kwargs: Any
    ) -> None:
        super().__init__(id="themepicker-app", **kwargs)
        self._theme_names = theme_names
        self._current_theme = current_theme
        self._preview_timer: Timer | None = None
        self._pending_preview: str | None = None

    def compose(self) -> ComposeResult:
        options = [
            Option(_build_option_text(name, name == self._current_theme), id=name)
            for name in self._theme_names
        ]
        with Vertical(id="themepicker-content"):
            yield NoMarkupStatic("Select Theme", classes="themepicker-title")
            yield OptionList(*options, id="themepicker-options")
            yield NoMarkupStatic(
                "↑↓ Preview  Enter Select  Esc Cancel", classes="themepicker-help"
            )

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        for i, name in enumerate(self._theme_names):
            if name == self._current_theme:
                option_list.highlighted = i
                break
        option_list.focus()

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if not event.option.id:
            return
        self._pending_preview = event.option.id
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(
            PREVIEW_DEBOUNCE_SECONDS, self._flush_preview
        )

    def _flush_preview(self) -> None:
        if self._pending_preview is None:
            return
        self.post_message(self.ThemePreviewed(self._pending_preview))
        self._pending_preview = None

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._pending_preview = None
        if event.option.id:
            self.post_message(self.ThemeSelected(event.option.id))

    def action_cancel(self) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
        self._pending_preview = None
        self.post_message(self.Cancelled(self._current_theme))
