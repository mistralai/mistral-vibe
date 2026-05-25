from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Vertical
from textual.events import MouseUp
from textual.validation import Length
from textual.widgets import Input, Link

from vibe.cli.clipboard import copy_selection_to_clipboard
from vibe.cli.textual_ui.widgets.banner.petit_chat import PetitChat
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.config import ProviderConfig
from vibe.core.telemetry.types import EntrypointMetadata
from vibe.setup.auth.api_key_persistence import (
    persist_api_key,
    resolve_api_key_provider,
)
from vibe.setup.onboarding.base import OnboardingScreen

# providers with a dedicated key creation page get a direct onboarding link
PROVIDER_HELP = {"mistral": ("https://console.mistral.ai/codestral/cli", "AI Studio")}
CONFIG_DOCS_URL = (
    "https://github.com/mistralai/mistral-vibe?tab=readme-ov-file#configuration"
)


class ApiKeyScreen(OnboardingScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    NEXT_SCREEN = None

    def __init__(
        self,
        provider: ProviderConfig | None = None,
        *,
        entrypoint_metadata: EntrypointMetadata | None = None,
    ) -> None:
        super().__init__()
        self.provider = resolve_api_key_provider(provider)
        self._entrypoint_metadata = entrypoint_metadata

    def _compose_provider_link(self) -> ComposeResult:
        if (help_info := PROVIDER_HELP.get(self.provider.name)) is None:
            return

        help_url, _ = help_info
        yield Link(help_url, url=help_url, id="api-key-provider-link")

    def _compose_config_docs(self) -> ComposeResult:
        yield NoMarkupStatic(
            "Learn more about Vibe configurations", id="config-docs-label"
        )
        yield Link(CONFIG_DOCS_URL, url=CONFIG_DOCS_URL, id="config-docs-link")

    def compose(self) -> ComposeResult:
        provider_name = self.provider.name.capitalize()
        help_info = PROVIDER_HELP.get(self.provider.name)
        help_name = help_info[1] if help_info is not None else "your provider"

        self.input_widget = Input(
            password=True,
            id="key",
            validators=[Length(minimum=1, failure_description="No API key provided.")],
        )
        input_box = Vertical(
            self.input_widget, id="input-box", classes="onboarding-card"
        )
        input_box.border_title = "Paste API key"

        with Vertical(id="api-key-outer", classes="onboarding-content"):
            with Center():
                with Vertical(id="api-key-panel", classes="onboarding-panel"):
                    yield PetitChat(id="api-key-chat", classes="onboarding-chat")
                    yield NoMarkupStatic(
                        f"Get your {provider_name} API key",
                        id="api-key-title",
                        classes="onboarding-heading",
                    )
                    yield NoMarkupStatic(
                        f"Visit {help_name} to generate or copy your Vibe key",
                        id="api-key-subtitle",
                    )
                    yield from self._compose_provider_link()
                    yield input_box
                    yield NoMarkupStatic("", id="feedback")
                    yield from self._compose_config_docs()

    def on_mount(self) -> None:
        self.input_widget.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        feedback = self.query_one("#feedback", NoMarkupStatic)
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
        self.app.exit(
            persist_api_key(
                self.provider, api_key, entrypoint_metadata=self._entrypoint_metadata
            )
        )

    def on_mouse_up(self, event: MouseUp) -> None:
        copy_selection_to_clipboard(self.app)
