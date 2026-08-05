from __future__ import annotations

from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from vibe.cli.textual_ui.constants import UNPINNED_ACTIVE_MODEL
from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.navigable_option_list import NavigableOptionList
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

# Option id for the "Default" (unpinned) row. Kept distinct from any model alias
# and non-empty so ``OptionList``'s truthiness guard still fires on select.
DEFAULT_OPTION_ID = "\x00default"


def _build_option_text(label: str, is_current: bool, *, hint: str = "") -> Text:
    text = Text(no_wrap=True)
    marker = "› " if is_current else "  "
    text.append(marker, style="green" if is_current else "")
    text.append(label, style="bold" if is_current else "")
    if hint:
        text.append(f"  {hint}", style="dim")
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
        self,
        model_aliases: list[str],
        current_model: str,
        *,
        is_pinned: bool,
        default_alias: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(id="modelpicker-app", **kwargs)
        self._model_aliases = model_aliases
        self._current_model = current_model
        self._is_pinned = is_pinned
        self._default_alias = default_alias

    def _is_alias_current(self, alias: str) -> bool:
        return self._is_pinned and alias == self._current_model

    def compose(self) -> ComposeResult:
        options = [
            Option(
                _build_option_text(
                    "Default",
                    not self._is_pinned,
                    hint=f"(currently {self._default_alias})",
                ),
                id=DEFAULT_OPTION_ID,
            ),
            *(
                Option(
                    _build_option_text(alias, self._is_alias_current(alias)), id=alias
                )
                for alias in self._model_aliases
            ),
        ]
        with Vertical(id="modelpicker-content"):
            yield NoMarkupStatic("Select Model", classes="modelpicker-title")
            yield NavigableOptionList(*options, id="modelpicker-options")
            yield NoMarkupStatic(
                shortcut_hint(
                    f"{shortcut('↑↓/jk')} Navigate  {shortcut('Enter')} Select  "
                    f"{shortcut('Esc')} Cancel"
                ),
                classes="modelpicker-help",
            )

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        # Pre-select the current choice: the pinned model, else the Default row.
        highlighted = 0
        if self._is_pinned:
            for i, alias in enumerate(self._model_aliases):
                if alias == self._current_model:
                    highlighted = i + 1  # offset by the leading Default row
                    break
        option_list.highlighted = highlighted
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not event.option.id:
            return
        alias = (
            UNPINNED_ACTIVE_MODEL
            if event.option.id == DEFAULT_OPTION_ID
            else event.option.id
        )
        self.post_message(self.ModelSelected(alias))

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())
