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

# from chefchat.cli.clipboard import copy_selection_to_clipboard # REMOVED: Circular import
from chefchat.core.config import GLOBAL_ENV_FILE, VibeConfig
from chefchat.setup.onboarding.base import OnboardingScreen

PROVIDER_HELP = {
    "mistral": ("https://console.mistral.ai/codestral/vibe", "Mistral AI Studio"),
    "openai": ("https://platform.openai.com/api-keys", "OpenAI Platform"),
    "anthropic": ("https://console.anthropic.com/settings/keys", "Anthropic Console"),
}
CONFIG_DOCS_URL = (
    "https://github.com/mistralai/mistral-vibe?tab=readme-ov-file#configuration"
)


def _save_api_key_to_env_file(env_key: str, api_key: str) -> None:
    GLOBAL_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    set_key(GLOBAL_ENV_FILE, env_key, api_key)


class ApiKeyScreen(OnboardingScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    NEXT_SCREEN = None

    def __init__(self) -> None:
        super().__init__()
        self.provider_name = "mistral"
        self.env_var = "MISTRAL_API_KEY"

    def _compose_provider_link(self, provider_name: str) -> ComposeResult:
        if self.provider_name not in PROVIDER_HELP:
            return

        help_url, help_name = PROVIDER_HELP[self.provider_name]
        yield Static(
            f"Procure your [orange1]Secret Ingredients[/] from the {help_name}:"
        )
        yield Center(
            Horizontal(
                Static("→ ", classes="link-chevron"),
                Link(help_url, url=help_url),
                classes="link-row",
            )
        )

    def _compose_config_docs(self) -> ComposeResult:
        yield Static("[dim]Learn more about ChefChat configuration:[/]")
        yield Horizontal(
            Static("→ ", classes="link-chevron"),
            Link(CONFIG_DOCS_URL, url=CONFIG_DOCS_URL),
            classes="link-row",
        )

    def compose(self) -> ComposeResult:
        # Get selected provider from app
        if hasattr(self.app, "selected_provider"):
            self.provider_name = self.app.selected_provider

        # Setenv vars mapping
        env_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "mistral": "MISTRAL_API_KEY",
        }
        self.env_var = env_map.get(self.provider_name, "MISTRAL_API_KEY")

        provider_display = self.provider_name.capitalize()

        self.input_widget = Input(
            password=True,
            id="key",
            placeholder=f"Add your {provider_display} API Key...",
            validators=[
                Length(minimum=1, failure_description="No Secret Sauce provided.")
            ],
        )

        with Vertical(id="api-key-outer"):
            yield Static("", classes="spacer")
            yield Center(
                Static(
                    f"[orange1]🧂 The Secret Sauce ({provider_display})[/]",
                    id="api-key-title",
                )
            )
            with Center():
                with Vertical(id="api-key-content"):
                    yield from self._compose_provider_link(self.provider_name)
                    yield Static("...and add it to your pantry below:", id="paste-hint")
                    yield Center(Horizontal(self.input_widget, id="input-box"))
                    yield Static("", id="feedback")
            yield Static("", classes="spacer")
            yield Vertical(
                Vertical(*self._compose_config_docs(), id="config-docs-group"),
                id="config-docs-section",
            )

    def on_mount(self) -> None:
        self.input_widget.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        feedback = self.query_one("#feedback", Static)
        input_box = self.query_one("#input-box")

        if event.validation_result is None:
            return

        input_box.remove_class("valid", "invalid")
        feedback.remove_class("error", "success")

        if event.validation_result.is_valid:
            feedback.update("Press Enter to submit ↵")
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
        # Save to .env
        os.environ[self.env_var] = api_key
        try:
            _save_api_key_to_env_file(self.env_var, api_key)

            # Also update local config to use this provider's model as active
            active_model = "devstral-2"  # Default fallback
            if self.provider_name == "openai":
                active_model = "gpt4o"
            elif self.provider_name == "anthropic":
                active_model = "claude-3-5-sonnet-20240620"

            updates = {"active_model": active_model}

            VibeConfig.save_updates(updates)

        except OSError as err:
            self.app.exit(f"save_error:{err}")
            return
        self.app.exit("completed")

    def on_mouse_up(self, event: MouseUp) -> None:
        pass  # Removed copy_selection_to_clipboard to break circular import
