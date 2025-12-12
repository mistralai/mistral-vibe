from __future__ import annotations

import os
from typing import ClassVar

from dotenv import set_key
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Horizontal, Vertical
from textual.events import MouseUp
from textual.validation import Length
from textual.widgets import Input, Link, Static

from vibe.cli.clipboard import copy_selection_to_clipboard
from vibe.core.config import GLOBAL_ENV_FILE, VibeConfig
from vibe.core.providers import PROVIDER_PRESETS, ProviderPreset
from vibe.setup.onboarding.base import OnboardingScreen

PROVIDER_HELP = {
    "mistral": ("https://console.mistral.ai/codestral/vibe", "Mistral AI Console"),
    "openai": ("https://platform.openai.com/api-keys", "OpenAI Platform"),
    "openrouter": ("https://openrouter.ai/keys", "OpenRouter Dashboard"),
    "together": ("https://api.together.xyz/settings/api-keys", "Together AI"),
    "groq": ("https://console.groq.com/keys", "Groq Console"),
}
CONFIG_DOCS_URL = (
    "https://github.com/mistralai/mistral-vibe?tab=readme-ov-file#configuration"
)


def _save_api_key_to_env_file(env_key: str, api_key: str) -> None:
    GLOBAL_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    set_key(GLOBAL_ENV_FILE, env_key, api_key)


class ApiKeyScreen(OnboardingScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        # Note: Enter for API key input is handled by on_input_submitted
        # This binding is only for local providers (no input widget)
        Binding("enter", "finish_local", "Finish", show=False),
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    NEXT_SCREEN = None

    def __init__(self) -> None:
        super().__init__()
        self.preset: ProviderPreset | None = None

    def _compose_provider_link(self, provider_name: str) -> ComposeResult:
        if not self.preset or self.preset.name not in PROVIDER_HELP:
            return

        help_url, help_name = PROVIDER_HELP[self.preset.name]
        yield Static(f"Grab your {provider_name} API key from the {help_name}:")
        yield Center(
            Horizontal(
                Static("\u2192 ", classes="link-chevron"),
                Link(help_url, url=help_url),
                classes="link-row",
            )
        )

    def _compose_config_docs(self) -> ComposeResult:
        yield Static("[dim]Learn more about Vibe configuration:[/]")
        yield Horizontal(
            Static("\u2192 ", classes="link-chevron"),
            Link(CONFIG_DOCS_URL, url=CONFIG_DOCS_URL),
            classes="link-row",
        )

    def compose(self) -> ComposeResult:
        # Get the selected provider from the app (set by ProviderSelectionScreen)
        selected_provider = getattr(self.app, "selected_provider", "mistral")
        self.preset = PROVIDER_PRESETS.get(selected_provider)

        # Check if this provider needs an API key
        if not self.preset or not self.preset.api_key_env_var:
            # Local providers don't need API keys - skip to completion
            yield from self._compose_local_provider_screen()
            return

        provider_name = self.preset.name.capitalize()

        self.input_widget = Input(
            password=True,
            id="key",
            placeholder="Paste your API key here",
            validators=[Length(minimum=1, failure_description="No API key provided.")],
        )

        with Vertical(id="api-key-outer"):
            yield Static("", classes="spacer")
            yield Center(Static("One last thing...", id="api-key-title"))
            with Center():
                with Vertical(id="api-key-content"):
                    yield from self._compose_provider_link(provider_name)
                    yield Static(
                        "...and paste it below to finish the setup:", id="paste-hint"
                    )
                    yield Center(Horizontal(self.input_widget, id="input-box"))
                    yield Static("", id="feedback")
            yield Static("", classes="spacer")
            yield Vertical(
                Vertical(*self._compose_config_docs(), id="config-docs-group"),
                id="config-docs-section",
            )

    def _compose_local_provider_screen(self) -> ComposeResult:
        """Compose screen for local providers that don't need API keys."""
        provider_name = self.preset.name.capitalize() if self.preset else "Local"
        notes = self.preset.notes if self.preset else ""
        api_base = self.preset.api_base if self.preset else "localhost"

        with Vertical(id="api-key-outer"):
            yield Static("", classes="spacer")
            yield Center(Static("You're all set!", id="api-key-title"))
            with Center():
                with Vertical(id="api-key-content"):
                    yield Static(
                        f"[bold]{provider_name}[/] doesn't require an API key.",
                        id="local-provider-info"
                    )
                    yield Static(f"[dim]API endpoint: {api_base}[/]")
                    if notes:
                        yield Static(f"[dim]{notes}[/]")
                    yield Static("", classes="spacer-small")
                    yield Static(
                        "Press [bold]Enter[/] to start using Vibe!",
                        id="paste-hint"
                    )
            yield Static("", classes="spacer")
            yield Vertical(
                Vertical(*self._compose_config_docs(), id="config-docs-group"),
                id="config-docs-section",
            )

    def on_mount(self) -> None:
        # For local providers, we have no input widget
        if hasattr(self, "input_widget"):
            self.input_widget.focus()
        else:
            self.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        feedback = self.query_one("#feedback", Static)
        input_box = self.query_one("#input-box")

        if event.validation_result is None:
            return

        input_box.remove_class("valid", "invalid")
        feedback.remove_class("error", "success")

        if event.validation_result.is_valid:
            feedback.update("Press Enter to submit \u21b5")
            feedback.add_class("success")
            input_box.add_class("valid")
            return

        descriptions = event.validation_result.failure_descriptions
        feedback.update(descriptions[0])
        feedback.add_class("error")
        input_box.add_class("invalid")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.validation_result and event.validation_result.is_valid:
            self._save_and_finish(event.value)

    def _save_and_finish(self, api_key: str) -> None:
        if not self.preset or not self.preset.api_key_env_var:
            # Local provider - just save config and exit
            self._save_provider_config()
            self.app.exit("completed")
            return

        env_key = self.preset.api_key_env_var
        os.environ[env_key] = api_key
        try:
            _save_api_key_to_env_file(env_key, api_key)
            self._save_provider_config()
        except OSError as err:
            self.app.exit(f"save_error:{err}")
            return
        self.app.exit("completed")

    def _save_provider_config(self) -> None:
        """Save the selected provider to config."""
        if not self.preset:
            return

        from vibe.core.providers import (
            create_model_config_from_preset,
            create_provider_config_from_preset,
        )

        try:
            provider_config = create_provider_config_from_preset(self.preset)
            model_config = create_model_config_from_preset(self.preset)

            VibeConfig.save_updates({
                "active_model": model_config.alias,
                "providers": [provider_config.model_dump()],
                "models": [model_config.model_dump()],
            })
        except Exception:
            # Config save is optional - API key is the important part
            pass

    def action_finish_local(self) -> None:
        """Handle Enter key for local providers (no API key needed)."""
        # Only trigger if this is a local provider (no api_key_env_var)
        if self.preset and not self.preset.api_key_env_var:
            self._save_and_finish("")

    def on_mouse_up(self, event: MouseUp) -> None:
        copy_selection_to_clipboard(self.app)
