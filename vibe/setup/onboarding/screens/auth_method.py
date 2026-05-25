from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Vertical

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.config import ProviderConfig
from vibe.setup.onboarding.base import OnboardingScreen

OPTION_BROWSER = 0
OPTION_MANUAL = 1


class AuthMethodScreen(OnboardingScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False, priority=True),
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, provider: ProviderConfig) -> None:
        super().__init__()
        self.provider = provider
        self._selected_index = OPTION_BROWSER
        self._option_widgets: list[NoMarkupStatic] = []
        self._help_widget: NoMarkupStatic

    def compose(self) -> ComposeResult:
        with Vertical(id="auth-method-content"):
            with Center():
                with Vertical(id="auth-method-card"):
                    yield NoMarkupStatic(
                        "How would you like to sign in?", id="auth-method-title"
                    )
                    yield NoMarkupStatic(
                        "Choose the setup that works best for you.",
                        id="auth-method-subtitle",
                    )
                    self._option_widgets = [
                        NoMarkupStatic("", classes="auth-method-option"),
                        NoMarkupStatic("", classes="auth-method-option"),
                    ]
                    yield from self._option_widgets
                    self._help_widget = NoMarkupStatic("", id="auth-method-help")
                    yield self._help_widget

    def on_mount(self) -> None:
        self._update_display()
        self.focus()

    def action_select(self) -> None:
        if self._selected_index == OPTION_BROWSER:
            self.action_browser()
            return
        self.action_manual()

    def action_manual(self) -> None:
        self.app.switch_screen("api_key")

    def action_browser(self) -> None:
        self.app.switch_screen("browser_sign_in")

    def action_move_up(self) -> None:
        self._selected_index = (self._selected_index - 1) % len(self._option_widgets)
        self._update_display()

    def action_move_down(self) -> None:
        self._selected_index = (self._selected_index + 1) % len(self._option_widgets)
        self._update_display()

    def _update_display(self) -> None:
        provider_name = self.provider.name.capitalize()
        options = [
            (
                "Sign in with your browser",
                f"Sign in to {provider_name} to finish setup automatically.",
            ),
            ("Use an API key", "Paste an existing API key to sign in manually."),
        ]

        for index, (widget, (title, description)) in enumerate(
            zip(self._option_widgets, options, strict=True)
        ):
            is_selected = index == self._selected_index
            prefix = "›" if is_selected else " "
            content = Text()
            content.append(f"{prefix} ")
            content.append(title, style="bold")
            content.append(f"\n  {description}")
            widget.update(content)
            widget.remove_class("selected")
            if is_selected:
                widget.add_class("selected")

        self._help_widget.update("↑↓ Choose · Enter to select · Esc Cancel")
