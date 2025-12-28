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
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    set_key(GLOBAL_ENV_FILE.path, env_key, api_key)


class ApiKeyScreen(OnboardingScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        # Note: Enter for API key input is handled by on_input_submitted
        # This binding is only for local providers (no input widget)
        Binding("enter", "finish_local", "Finish", show=False),
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
        # Shift+Enter to skip validation
        Binding("shift+enter", "skip_validation", "Skip Validation", show=False),
    ]

    NEXT_SCREEN = None

    def __init__(self) -> None:
        super().__init__()
        self.preset: ProviderPreset | None = None
        self._validating = False

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
                    yield Static(
                        "[dim]Tip: Press Shift+Enter to skip validation[/]",
                        id="skip-hint"
                    )
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
        if not (event.validation_result and event.validation_result.is_valid):
            return

        if self._validating:
            return  # Prevent double submission

        self._validating = True
        self._validate_and_save(event.value)

    def _validate_and_save(self, api_key: str) -> None:
        """Validate API key and save if valid."""
        feedback = self.query_one("#feedback", Static)
        input_box = self.query_one("#input-box")

        # Clear previous feedback
        feedback.update("")
        feedback.remove_class("error", "success")

        # Show validation in progress
        feedback.update("Validating API key...")
        feedback.add_class("success")

        try:
            self._validate_api_key(api_key)
            feedback.update("API key validated successfully! \u2713")
            feedback.add_class("success")
            self._save_and_finish(api_key)
        except ValueError as err:
            # Invalid API key
            feedback.update(f"Invalid API key: {err}")
            feedback.add_class("error")
            input_box.remove_class("valid")
            input_box.add_class("invalid")
            self._validating = False
            if hasattr(self, "input_widget"):
                self.input_widget.focus()
        except ConnectionError as err:
            # Network error - offer to skip
            feedback.update(
                f"[yellow]Network error:[/] {err}\n"
                "[dim]Press Shift+Enter to skip validation[/]"
            )
            feedback.add_class("error")
            self._validating = False
            if hasattr(self, "input_widget"):
                self.input_widget.focus()
        except Exception as err:
            # Other errors - offer to skip
            feedback.update(
                f"[yellow]Validation error:[/] {err}\n"
                "[dim]Press Shift+Enter to skip validation[/]"
            )
            feedback.add_class("error")
            self._validating = False
            if hasattr(self, "input_widget"):
                self.input_widget.focus()

    def _validate_api_key(self, api_key: str) -> None:
        """
        Validate API key by making a minimal API call.
        Raises ValueError if the API key is invalid.
        Raises ConnectionError if there's a network issue.
        """
        if not self.preset:
            return

        # Use the appropriate validation method based on provider
        if self.preset.backend == "mistral":
            self._validate_mistral_key(api_key)
        else:
            # Generic OpenAI-compatible validation
            self._validate_openai_compatible_key(api_key)

    def _validate_mistral_key(self, api_key: str) -> None:
        """Validate Mistral API key."""
        try:
            from mistralai import Mistral
        except ImportError:
            # If mistralai SDK not available, skip validation
            return

        try:
            client = Mistral(api_key=api_key)
            client.models.list()
        except Exception as err:
            error_msg = str(err).lower()
            if any(keyword in error_msg for keyword in ["unauthorized", "401", "api key", "invalid"]):
                raise ValueError("Authentication failed - check your API key") from err
            elif any(keyword in error_msg for keyword in ["connection", "network", "timeout", "unreachable"]):
                raise ConnectionError(f"Network error: {err}") from err
            else:
                raise

    def _validate_openai_compatible_key(self, api_key: str) -> None:
        """Validate OpenAI-compatible API key (works for OpenAI, Groq, Together, OpenRouter)."""
        import httpx

        if not self.preset:
            return

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Add provider-specific headers
        if self.preset.name == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/mistralai/mistral-vibe"
            headers["X-Title"] = "Mistral Vibe CLI"

        # Use /models endpoint for validation (lightweight and universal)
        url = f"{self.preset.api_base.rstrip('/v1')}/v1/models"

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, headers=headers)

                if response.status_code == 401 or response.status_code == 403:
                    raise ValueError("Authentication failed - check your API key")
                elif response.status_code >= 500:
                    raise ConnectionError(f"Server error ({response.status_code})")
                elif response.status_code >= 400:
                    raise ValueError(f"API error ({response.status_code}): {response.text[:200]}")

                # Success if we get a 200 response
                response.raise_for_status()

        except httpx.ConnectError as err:
            raise ConnectionError(f"Cannot connect to {self.preset.name} API") from err
        except httpx.TimeoutException as err:
            raise ConnectionError(f"Connection timeout") from err
        except httpx.RequestError as err:
            raise ConnectionError(f"Network error: {err}") from err

    def action_skip_validation(self) -> None:
        """Skip validation and save API key directly (Shift+Enter)."""
        if not hasattr(self, "input_widget"):
            return

        api_key = self.input_widget.value.strip()
        if not api_key:
            feedback = self.query_one("#feedback", Static)
            feedback.update("Please enter an API key first")
            feedback.add_class("error")
            return

        feedback = self.query_one("#feedback", Static)
        feedback.update("Skipping validation...")
        feedback.add_class("success")
        self._save_and_finish(api_key)

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
