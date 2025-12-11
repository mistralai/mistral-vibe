"""ChefChat Provider Selection Screen.

Choose your AI provider (OpenAI, Mistral, Anthropic) with Tab navigation.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Grid, Vertical
from textual.widgets import Button, Static

from chefchat.setup.onboarding.base import OnboardingScreen


class ProviderSelectionScreen(OnboardingScreen):
    """Screen for selecting AI provider.

    Uses Tab key to navigate between provider options.
    Enter or click to select a provider.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("tab", "focus_next", "Next", show=True),
        Binding("shift+tab", "focus_previous", "Previous", show=True),
        Binding("1", "select_openai", "OpenAI", show=False),
        Binding("2", "select_anthropic", "Anthropic", show=False),
        Binding("3", "select_mistral", "Mistral", show=False),
    ]

    NEXT_SCREEN = "api_key"

    def compose(self) -> ComposeResult:
        """Compose the provider selection UI."""
        with Vertical(id="provider-select-container"):
            yield Static("🍳 Choose Your Sous Chef", id="provider-title")
            yield Static(
                "Which AI brain powers your kitchen?\n"
                "[dim]Use Tab to navigate, Enter to select[/]",
                id="provider-subtitle",
            )

            with Center():
                with Grid(id="provider-grid"):
                    yield Button(
                        "🤖 OpenAI",
                        id="openai",
                        variant="primary",
                        classes="provider-button",
                    )
                    yield Button(
                        "🧠 Anthropic",
                        id="anthropic",
                        variant="warning",
                        classes="provider-button",
                    )
                    yield Button(
                        "🎯 Mistral",
                        id="mistral",
                        variant="success",
                        classes="provider-button",
                    )

            yield Center(
                Static(
                    "[dim]Press 1, 2, or 3 for quick selection[/]\n"
                    "[dim]You can change this later in config[/]",
                    classes="muted",
                )
            )

    def on_mount(self) -> None:
        """Focus the first button on mount."""
        try:
            first_button = self.query_one("#openai", Button)
            first_button.focus()
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle provider button press."""
        self.app.selected_provider = event.button.id
        self.action_next()

    def action_select_openai(self) -> None:
        """Quick select OpenAI with key 1."""
        self.app.selected_provider = "openai"
        self.action_next()

    def action_select_anthropic(self) -> None:
        """Quick select Anthropic with key 2."""
        self.app.selected_provider = "anthropic"
        self.action_next()

    def action_select_mistral(self) -> None:
        """Quick select Mistral with key 3."""
        self.app.selected_provider = "mistral"
        self.action_next()
