from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, Static

from vibe.core.providers import PROVIDER_PRESETS, ProviderPreset
from vibe.setup.onboarding.base import OnboardingScreen


# Provider groups for display
LOCAL_PROVIDERS = ["ollama", "llamacpp", "vllm", "localai", "lmstudio"]
REMOTE_PROVIDERS = ["mistral", "openai", "openrouter", "together", "groq"]


class ProviderButton(Button):
    """A button representing a provider choice."""

    def __init__(self, preset: ProviderPreset, **kwargs) -> None:
        super().__init__(preset.name.capitalize(), id=f"provider-{preset.name}", **kwargs)
        self.preset = preset


class ProviderSelectionScreen(OnboardingScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("up", "focus_previous", "Previous", show=False),
        Binding("down", "focus_next", "Next", show=False),
        Binding("k", "focus_previous", "Previous", show=False),
        Binding("j", "focus_next", "Next", show=False),
    ]

    NEXT_SCREEN = "api_key"

    def __init__(self) -> None:
        super().__init__()
        self.selected_provider: str = "mistral"

    def compose(self) -> ComposeResult:
        with Vertical(id="provider-outer"):
            yield Static("", classes="spacer")
            yield Center(Static("Choose your LLM provider", id="provider-title"))
            yield Static("", classes="spacer-small")

            with Center():
                with ScrollableContainer(id="provider-container"):
                    # Remote providers (cloud APIs)
                    yield Static("[bold]Cloud Providers[/]", classes="provider-section-title")
                    with Horizontal(classes="provider-row"):
                        for name in REMOTE_PROVIDERS[:3]:
                            if preset := PROVIDER_PRESETS.get(name):
                                btn = ProviderButton(preset, classes="provider-btn")
                                if name == "mistral":
                                    btn.add_class("selected")
                                yield btn
                    with Horizontal(classes="provider-row"):
                        for name in REMOTE_PROVIDERS[3:]:
                            if preset := PROVIDER_PRESETS.get(name):
                                yield ProviderButton(preset, classes="provider-btn")

                    yield Static("", classes="spacer-small")

                    # Local providers
                    yield Static("[bold]Local Providers[/]", classes="provider-section-title")
                    with Horizontal(classes="provider-row"):
                        for name in LOCAL_PROVIDERS[:3]:
                            if preset := PROVIDER_PRESETS.get(name):
                                yield ProviderButton(preset, classes="provider-btn")
                    with Horizontal(classes="provider-row"):
                        for name in LOCAL_PROVIDERS[3:]:
                            if preset := PROVIDER_PRESETS.get(name):
                                yield ProviderButton(preset, classes="provider-btn")

            yield Static("", classes="spacer-small")
            with Center():
                yield Static("", id="provider-description")

            yield Static("", classes="spacer")
            with Center():
                yield Static(
                    "[dim]\u2191\u2193 or j/k to navigate \u2022 Enter to select[/]",
                    id="provider-hint"
                )

    def on_mount(self) -> None:
        # Focus the first provider button (Mistral)
        first_btn = self.query_one("#provider-mistral", ProviderButton)
        first_btn.focus()
        self._update_description(first_btn.preset)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if isinstance(event.button, ProviderButton):
            self._select_provider(event.button)

    def on_button_focused(self, event: Button.Focused) -> None:
        if isinstance(event.button, ProviderButton):
            self._update_description(event.button.preset)

    def _update_description(self, preset: ProviderPreset) -> None:
        desc = self.query_one("#provider-description", Static)
        lines = [
            f"[bold]{preset.description}[/]",
            f"[dim]API: {preset.api_base}[/]",
        ]
        if preset.api_key_env_var:
            lines.append(f"[dim]Requires: ${preset.api_key_env_var}[/]")
        if preset.notes:
            lines.append(f"[dim italic]{preset.notes}[/]")
        desc.update("\n".join(lines))

    def _select_provider(self, button: ProviderButton) -> None:
        # Remove selected class from all buttons
        for btn in self.query(ProviderButton):
            btn.remove_class("selected")

        # Add selected class to clicked button
        button.add_class("selected")
        self.selected_provider = button.preset.name

        # Store in app for later use
        self.app.selected_provider = button.preset.name  # type: ignore

        # Move to next screen
        self.action_next()

    def action_next(self) -> None:
        # Store selection before moving on
        if hasattr(self.app, "selected_provider"):
            pass  # Already stored
        else:
            self.app.selected_provider = self.selected_provider  # type: ignore
        super().action_next()
