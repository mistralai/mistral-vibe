from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.events import DescendantBlur
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig


class ConfigApp(Container):
    """Settings panel with navigatable option picker."""

    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False)
    ]

    class SettingChanged(Message):
        def __init__(self, key: str, value: str) -> None:
            super().__init__()
            self.key = key
            self.value = value

    class ConfigClosed(Message):
        def __init__(self, changes: dict[str, str | bool]) -> None:
            super().__init__()
            self.changes = changes

    class OpenModelPicker(Message):
        pass

    def __init__(self, config: VibeConfig) -> None:
        super().__init__(id="config-app")
        self.config = config
        self.changes: dict[str, str] = {}
        self._toggle_settings: list[tuple[str, str]] = [
            ("autocopy_to_clipboard", "Auto-copy"),
            (
                "file_watcher_for_autocomplete",
                "Autocomplete watcher (may delay first autocompletion)",
            ),
        ]

    def _get_current_model(self) -> str:
        return str(getattr(self.config, "active_model", ""))

    def _get_toggle_value(self, key: str) -> str:
        if key in self.changes:
            return self.changes[key]
        raw = getattr(self.config, key, False)
        if isinstance(raw, bool):
            return "On" if raw else "Off"
        return str(raw)

    def _model_prompt(self) -> Text:
        text = Text(no_wrap=True)
        text.append("Model: ")
        text.append(self._get_current_model(), style="bold")
        return text

    def _toggle_prompt(self, key: str, label: str) -> Text:
        value = self._get_toggle_value(key)
        text = Text(no_wrap=True)
        text.append(f"{label}: ")
        if value == "On":
            text.append("On", style="green bold")
        else:
            text.append("Off", style="dim")
        return text

    def compose(self) -> ComposeResult:
        options: list[Option] = [Option(self._model_prompt(), id="action:active_model")]
        for key, label in self._toggle_settings:
            options.append(Option(self._toggle_prompt(key, label), id=f"toggle:{key}"))

        with Vertical(id="config-content"):
            yield NoMarkupStatic("Settings", classes="settings-title")
            yield NoMarkupStatic("")
            yield OptionList(*options, id="config-options")
            yield NoMarkupStatic("")
            yield NoMarkupStatic(
                "↑↓ Navigate  Enter Select/Toggle  Esc Exit", classes="settings-help"
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_descendant_blur(self, _event: DescendantBlur) -> None:
        self.query_one(OptionList).focus()

    def _refresh_options(self) -> None:
        option_list = self.query_one(OptionList)
        option_list.replace_option_prompt("action:active_model", self._model_prompt())
        for key, label in self._toggle_settings:
            option_list.replace_option_prompt(
                f"toggle:{key}", self._toggle_prompt(key, label)
            )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if not option_id:
            return

        if option_id == "action:active_model":
            self.post_message(self.OpenModelPicker())
            return

        if option_id.startswith("toggle:"):
            key = option_id.removeprefix("toggle:")
            current = self._get_toggle_value(key)
            new_value = "Off" if current == "On" else "On"
            self.changes[key] = new_value
            self.post_message(self.SettingChanged(key=key, value=new_value))
            self._refresh_options()

    def _convert_changes_for_save(self) -> dict[str, str | bool]:
        result: dict[str, str | bool] = {}
        for key, value in self.changes.items():
            if value in {"On", "Off"}:
                result[key] = value == "On"
            else:
                result[key] = value
        return result

    def action_close(self) -> None:
        self.post_message(self.ConfigClosed(changes=self._convert_changes_for_save()))
