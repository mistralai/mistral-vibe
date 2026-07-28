from __future__ import annotations

from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import Input, Static

from vibe.app_server.config import ProxySettingsView
from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.vscode_compat import VscodeCompatInput


class ProxySetupApp(Container):
    can_focus = True
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "focus_previous", "Up", show=False),
        Binding("down", "focus_next", "Down", show=False),
    ]

    class ProxySetupClosed(Message):
        def __init__(
            self, saved: bool, changes: dict[str, str | None] | None = None
        ) -> None:
            super().__init__()
            self.saved = saved
            self.changes = changes or {}

    def __init__(self, settings: ProxySettingsView) -> None:
        super().__init__(id="proxysetup-app")
        self._settings = settings
        self.inputs: dict[str, Input] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="proxysetup-content"):
            yield NoMarkupStatic("Proxy Configuration", classes="settings-title")

            for key, description in self._settings.descriptions.items():
                yield Static(f"[bold $primary]{key}[/]", classes="proxy-label-line")

                initial_value = self._settings.values.get(key) or ""
                input_widget = VscodeCompatInput(
                    value=initial_value,
                    placeholder=description,
                    id=f"proxy-input-{key}",
                    classes="proxy-input",
                )
                self.inputs[key] = input_widget
                yield input_widget

            yield NoMarkupStatic(
                shortcut_hint(
                    f"{shortcut('↑↓')} navigate  {shortcut('Enter')} save & exit  "
                    f"{shortcut('Esc')} cancel"
                ),
                classes="settings-help",
            )

    def focus(self, scroll_visible: bool = True) -> ProxySetupApp:
        """Override focus to focus the first input widget."""
        if self.inputs:
            first_input = list(self.inputs.values())[0]
            first_input.focus(scroll_visible=scroll_visible)
        else:
            super().focus(scroll_visible=scroll_visible)
        return self

    def action_focus_next(self) -> None:
        inputs = list(self.inputs.values())
        focused = self.screen.focused
        if focused is not None and isinstance(focused, Input) and focused in inputs:
            idx = inputs.index(focused)
            next_idx = (idx + 1) % len(inputs)
            inputs[next_idx].focus()

    def action_focus_previous(self) -> None:
        inputs = list(self.inputs.values())
        focused = self.screen.focused
        if focused is not None and isinstance(focused, Input) and focused in inputs:
            idx = inputs.index(focused)
            prev_idx = (idx - 1) % len(inputs)
            inputs[prev_idx].focus()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._save_and_close()

    def on_blur(self, _event: events.Blur) -> None:
        self.call_after_refresh(self._refocus_if_needed)

    def on_input_blurred(self, _event: Input.Blurred) -> None:
        self.call_after_refresh(self._refocus_if_needed)

    def _refocus_if_needed(self) -> None:
        if self.has_focus or any(inp.has_focus for inp in self.inputs.values()):
            return
        self.focus()

    def _save_and_close(self) -> None:
        changes = {
            key: value or None
            for key, input_widget in self.inputs.items()
            if (value := input_widget.value.strip())
            != (self._settings.values.get(key) or "")
        }
        self.post_message(self.ProxySetupClosed(saved=True, changes=changes))

    def action_close(self) -> None:
        self.post_message(self.ProxySetupClosed(saved=False))
