from __future__ import annotations

from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import Input, Static

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.proxy_setup import (
    SUPPORTED_PROXY_VARS,
    get_current_proxy_settings,
    set_proxy_var,
    unset_proxy_var,
)


class ProxySetupApp(Container):
    can_focus = True
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "focus_previous", "Up", show=False),
        Binding("down", "focus_next", "Down", show=False),
    ]

    class ProxySetupClosed(Message):
        def __init__(self, saved: bool, error: str | None = None) -> None:
            super().__init__()
            self.saved = saved
            self.error = error

    def __init__(self) -> None:
        super().__init__(id="proxysetup-app")
        self.inputs: dict[str, Input] = {}
        self.initial_values: dict[str, str | None] = {}

    def compose(self) -> ComposeResult:
        self.initial_values = get_current_proxy_settings()

        with Vertical(id="proxysetup-content"):
            yield NoMarkupStatic("Proxy Configuration", classes="settings-title")
            yield NoMarkupStatic("")

            for key, description in SUPPORTED_PROXY_VARS.items():
                yield Static(
                    f"[bold ansi_blue]{key}[/]  [dim]{description}[/dim]",
                    classes="proxy-label-line",
                )

                initial_value = self.initial_values.get(key) or ""
                input_widget = Input(
                    value=initial_value,
                    placeholder="NOT SET",
                    id=f"proxy-input-{key}",
                    classes="proxy-input",
                )
                self.inputs[key] = input_widget
                yield input_widget

                yield NoMarkupStatic("")

            yield NoMarkupStatic(
                "↑↓ navigate  Enter save & exit  ESC cancel", classes="settings-help"
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

    def on_input_submitted(self, event: Input.Submitted) -> None:
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
        try:
            for key, input_widget in self.inputs.items():
                new_value = input_widget.value.strip()
                old_value = self.initial_values.get(key) or ""

                if new_value != old_value:
                    if new_value:
                        set_proxy_var(key, new_value)
                    else:
                        unset_proxy_var(key)
        except Exception as e:
            self.post_message(self.ProxySetupClosed(saved=False, error=str(e)))
            return

        self.post_message(self.ProxySetupClosed(saved=True))

    def action_close(self) -> None:
        self.post_message(self.ProxySetupClosed(saved=False))
