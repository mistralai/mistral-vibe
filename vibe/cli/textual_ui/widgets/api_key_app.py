from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from dotenv import set_key
from mistralai import Mistral
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.validation import Length
from textual.widgets import Input, Static

from vibe.core.paths.global_paths import GLOBAL_ENV_FILE
from vibe.core.utils import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig


def _save_api_key_to_env_file(env_key: str, api_key: str) -> None:
    """Save API key to the global .env file."""
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    set_key(GLOBAL_ENV_FILE.path, env_key, api_key)


class Api_KeyApp(Container):
    """Widget for updating API key."""

    can_focus = True
    can_focus_children = True
    changes: dict[str, str] = {}
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Esc", show=False)
    ]

    class Api_KeySubmitted(Message):
        """Posted when user submits an API key."""

        def __init__(self, api_key: str) -> None:
            super().__init__()
            self.api_key = api_key

    # class Api_KeyCancelled(Message):
    #     """Posted when user cancels API key update."""

    #     pass

    class Api_KeyCancelled(Message):
        def __init__(self, changes: dict[str, str]) -> None:
            super().__init__()
            self.changes = changes

    def __init__(self, config: VibeConfig) -> None:
        super().__init__(id="api-key-app")
        self.config = config
        self.input_widget: Input | None = None
        self.title_widget: Static | None = None
        self.help_widget: Static | None = None
        self.feedback_widget: Static | None = None
        self.changes: dict[str, str] = {}
        from vibe.core.utils import logger

    def compose(self) -> ComposeResult:
        with Vertical(id="config-content"):
            self.title_widget = Static("Update API Key", classes="settings-title")
            yield self.title_widget

            yield Static("")

            self.input_widget = Input(
                password=True,
                id="api-key-input",
                placeholder="Paste your API key here",
                validators=[
                    Length(minimum=1, failure_description="No API key provided.")
                ],
            )
            yield self.input_widget

            yield Static("")

            self.help_widget = Static("↵ submit  ESC exit", classes="settings-help")
            yield self.help_widget

            self.feedback_widget = Static(
                "", id="api-key-feedback", classes="api-key-feedback"
            )
            yield self.feedback_widget

    def on_mount(self) -> None:
        if self.input_widget:
            self.input_widget.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle validation feedback for input changes."""
        feedback = self.query_one("#api-key-feedback", Static)

        if event.validation_result is None:
            return

        input_widget = self.query_one("#api-key-input", Input)
        input_widget.remove_class("valid", "invalid")
        feedback.remove_class("error", "success")

        if event.validation_result.is_valid:
            feedback.update("Press Enter to submit ↵")
            feedback.add_class("success")
            input_widget.add_class("valid")
            return

        descriptions = event.validation_result.failure_descriptions
        feedback.update(descriptions[0])
        feedback.add_class("error")
        input_widget.add_class("invalid")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle API key submission."""
        if not (event.validation_result and event.validation_result.is_valid):
            return

        feedback = self.query_one("#api-key-feedback", Static)
        input_widget = self.query_one("#api-key-input", Input)

        feedback.update("")
        feedback.remove_class("error", "success", "info")
        input_widget.remove_class("valid", "invalid")

        feedback.update("Validating API key...")
        feedback.add_class("info")

        try:
            self._validate_api_key(event.value)
        except ValueError:
            feedback.update("Invalid API key. Please try again.")
            feedback.add_class("error")
            input_widget.remove_class("valid")
            input_widget.add_class("invalid")
            if self.input_widget:
                self.input_widget.focus()
            return
        except Exception as err:
            feedback.update(f"Failed to validate API key: {err}")
            feedback.remove_class("info", "success")
            feedback.add_class("error")
            input_widget.remove_class("valid")
            input_widget.add_class("invalid")
            if self.input_widget:
                self.input_widget.focus()
            return

        self.post_message(self.Api_KeySubmitted(api_key=event.value))

    def _validate_api_key(self, api_key: str) -> None:
        """Validate the API key by making a test API call.

        Raises ValueError if the API key is invalid.
        """
        client = Mistral(api_key=api_key)

        try:
            client.models.list()
        except Exception as err:
            message = str(err).lower()
            if "unauthorized" in message or "401" in message or "api key" in message:
                raise ValueError("Invalid API key") from err
            raise

    def on_blur(self, event: events.Blur) -> None:
        """Keep focus on the input widget."""
        if self.input_widget:
            self.call_after_refresh(self.input_widget.focus)

    class Api_KeyClosed(Message):
        def __init__(self, changes: dict[str, str]) -> None:
            super().__init__()
            self.changes = changes

    def action_close(self) -> None:
        self.post_message(self.Api_KeyClosed(changes=self.changes.copy()))
