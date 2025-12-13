from __future__ import annotations

import os
from typing import TYPE_CHECKING, ClassVar, TypedDict

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.theme import BUILTIN_THEMES
from textual.widgets import Input, Static

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig

THEMES = sorted(k for k in BUILTIN_THEMES if k != "textual-ansi")


class SettingDefinition(TypedDict):
    key: str
    label: str
    type: str
    options: list[str]
    value: str


def _mask_api_key(api_key: str | None) -> str:
    """Mask API key showing only first 3 and last 4 characters."""
    if not api_key:
        return "Not set"
    if len(api_key) <= 7:
        return "•" * len(api_key)
    return f"{api_key[:3]}...{api_key[-4:]}"


class ConfigApp(Container):
    can_focus = True
    can_focus_children = False

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("space", "toggle_setting", "Toggle", show=False),
        Binding("enter", "cycle", "Next", show=False),
    ]

    class SettingChanged(Message):
        def __init__(self, key: str, value: str) -> None:
            super().__init__()
            self.key = key
            self.value = value

    class ConfigClosed(Message):
        def __init__(self, changes: dict[str, str]) -> None:
            super().__init__()
            self.changes = changes

    def __init__(self, config: VibeConfig) -> None:
        super().__init__(id="config-app")
        self.config = config
        self.selected_index = 0
        self.changes: dict[str, str] = {}
        self._editing_api_key = False
        self._api_key_input: Input | None = None

        # Get current API key value
        try:
            active_model = self.config.get_active_model()
            provider = self.config.get_provider_for_model(active_model)
            api_key_env_var = provider.api_key_env_var
            current_api_key = os.getenv(api_key_env_var) if api_key_env_var else None
        except (ValueError, AttributeError):
            current_api_key = None

        self.settings: list[SettingDefinition] = [
            {
                "key": "active_model",
                "label": "Model",
                "type": "cycle",
                "options": [m.alias for m in self.config.models],
                "value": self.config.active_model,
            },
            {
                "key": "textual_theme",
                "label": "Theme",
                "type": "cycle",
                "options": THEMES,
                "value": self.config.textual_theme,
            },
            {
                "key": "api_key",
                "label": "API Key",
                "type": "api_key",
                "options": [],
                "value": current_api_key or "",
            },
        ]

        self.title_widget: Static | None = None
        self.setting_widgets: list[Static] = []
        self.help_widget: Static | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="config-content"):
            self.title_widget = Static("Settings", classes="settings-title")
            yield self.title_widget

            yield Static("")

            for _ in self.settings:
                widget = Static("", classes="settings-option")
                self.setting_widgets.append(widget)
                yield widget

            # Hidden input for API key editing
            self._api_key_input = Input(
                password=True,
                placeholder="Enter API key",
                id="api-key-input",
                classes="settings-api-key-input",
            )
            yield self._api_key_input

            yield Static("")

            self.help_widget = Static(
                "↑↓ navigate  Space/Enter toggle  ESC exit", classes="settings-help"
            )
            yield self.help_widget

    def on_mount(self) -> None:
        self._update_display()
        if self._api_key_input:
            self._api_key_input.display = False
        self.focus()

    def _update_display(self) -> None:
        for i, (setting, widget) in enumerate(
            zip(self.settings, self.setting_widgets, strict=True)
        ):
            is_selected = i == self.selected_index
            is_editing = is_selected and setting["type"] == "api_key" and self._editing_api_key
            cursor = "› " if is_selected else "  "

            label: str = setting["label"]
            value: str = self.changes.get(setting["key"], setting["value"])

            # Mask API key for display
            if setting["type"] == "api_key":
                display_value = _mask_api_key(value) if not is_editing else "[editing...]"
            else:
                display_value = value

            text = f"{cursor}{label}: {display_value}"

            widget.update(text)

            widget.remove_class("settings-cursor-selected")
            widget.remove_class("settings-value-cycle-selected")
            widget.remove_class("settings-value-cycle-unselected")

            if is_selected:
                widget.add_class("settings-value-cycle-selected")
            else:
                widget.add_class("settings-value-cycle-unselected")

        # Show/hide API key input
        if self._api_key_input:
            if self._editing_api_key:
                self._api_key_input.display = True
                self._api_key_input.focus()
            else:
                self._api_key_input.display = False

        # Update help text
        if self.help_widget:
            if (
                self.settings
                and self.settings[self.selected_index]["type"] == "api_key"
                and not self._editing_api_key
            ):
                help_text = "↑↓ navigate  Enter to edit  ESC exit"
            elif self._editing_api_key:
                help_text = "Type API key  Enter to save  ESC to cancel"
            else:
                help_text = "↑↓ navigate  Space/Enter toggle  ESC exit"
            self.help_widget.update(help_text)

    def action_move_up(self) -> None:
        if self._editing_api_key:
            self._editing_api_key = False
        self.selected_index = (self.selected_index - 1) % len(self.settings)
        self._update_display()

    def action_move_down(self) -> None:
        if self._editing_api_key:
            self._editing_api_key = False
        self.selected_index = (self.selected_index + 1) % len(self.settings)
        self._update_display()

    def action_toggle_setting(self) -> None:
        setting = self.settings[self.selected_index]
        key: str = setting["key"]

        # Handle API key editing
        if setting["type"] == "api_key":
            if not self._editing_api_key:
                # Start editing
                self._editing_api_key = True
                if self._api_key_input:
                    current_value = self.changes.get(key, setting["value"])
                    self._api_key_input.value = current_value
                self._update_display()
            return

        # Handle cycle type settings
        current: str = self.changes.get(key, setting["value"])

        options: list[str] = setting["options"]
        new_value: str
        try:
            current_idx = options.index(current)
            next_idx = (current_idx + 1) % len(options)
            new_value = options[next_idx]
        except (ValueError, IndexError):
            new_value = options[0] if options else current

        self.changes[key] = new_value

        self.post_message(self.SettingChanged(key=key, value=new_value))

        self._update_display()

    def action_cycle(self) -> None:
        self.action_toggle_setting()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle API key input submission."""
        if event.input == self._api_key_input and self._editing_api_key:
            setting = self.settings[self.selected_index]
            key: str = setting["key"]
            new_value: str = event.value.strip()

            if new_value:
                self.changes[key] = new_value
                self.post_message(self.SettingChanged(key=key, value=new_value))

            self._editing_api_key = False
            self._update_display()
            self.focus()

    def action_close(self) -> None:
        # Cancel API key editing if active
        if self._editing_api_key:
            self._editing_api_key = False
            self._update_display()
            return

        self.post_message(self.ConfigClosed(changes=self.changes.copy()))

    def on_blur(self, event: events.Blur) -> None:
        # Don't refocus if we're editing API key
        if self._editing_api_key and event.widget == self._api_key_input:
            return
        self.call_after_refresh(self.focus)
