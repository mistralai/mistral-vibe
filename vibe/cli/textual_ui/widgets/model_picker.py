from __future__ import annotations

from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic


def _build_option_text(alias: str, is_current: bool) -> Text:
    text = Text(no_wrap=True)
    marker = "› " if is_current else "  "
    style = "bold" if is_current else ""
    text.append(marker, style="green" if is_current else "")
    text.append(alias, style=style)
    return text


class ModelPickerApp(Container):
    """Model picker bottom app for selecting the active model."""

    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False)
    ]

    class ModelSelected(Message):
        def __init__(self, alias: str) -> None:
            self.alias = alias
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(
        self, model_aliases: list[str], current_model: str, **kwargs: Any
    ) -> None:
        super().__init__(id="modelpicker-app", **kwargs)
        self._model_aliases = model_aliases
        self._current_model = current_model

    def compose(self) -> ComposeResult:
        options = [
            Option(_build_option_text(alias, alias == self._current_model), id=alias)
            for alias in self._model_aliases
        ]
        with Vertical(id="modelpicker-content"):
            yield NoMarkupStatic("Select Model", classes="modelpicker-title")
            yield OptionList(*options, id="modelpicker-options")
            yield NoMarkupStatic(
                "↑↓ Navigate  Enter Select  Esc Cancel", classes="modelpicker-help"
            )

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        # Pre-select the current model
        for i, alias in enumerate(self._model_aliases):
            if alias == self._current_model:
                option_list.highlighted = i
                break
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.post_message(self.ModelSelected(event.option.id))

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())
