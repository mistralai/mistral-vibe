from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from mistralai import Mistral
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.message import Message
from textual.validation import Length
from textual.widget import Widget
from textual.widgets import Input, Static

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig


class ApiKeyApp(Widget):
    """Widget for updating and validating an API key."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Esc", show=False)
    ]

    class ApiKeySubmitted(Message):
        """Posted when a valid API key is submitted."""

        def __init__(self, api_key: str) -> None:
            super().__init__()
            self.api_key = api_key

    class ApiKeyClosed(Message):
        """Posted when the widget is closed."""

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

            self.feedback_widget = Static(
                "", id="api-key-feedback", classes="api-key-feedback"
            )
            yield self.feedback_widget

            self.help_widget = Static("↵ submit   ESC exit", classes="settings-help")
            yield self.help_widget

    def on_mount(self) -> None:
        if self.input_widget:
            self.input_widget.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Update validation feedback while typing."""
        if not self.input_widget or not self.feedback_widget:
            return

        if event.validation_result is None:
            return

        self.input_widget.remove_class("valid", "invalid")
        self.feedback_widget.remove_class("error", "success")

        if event.validation_result.is_valid:
            self.feedback_widget.update("Press Enter to submit ↵")
            self.feedback_widget.add_class("success")
            self.input_widget.add_class("valid")
            return

        description = event.validation_result.failure_descriptions[0]
        self.feedback_widget.update(description)
        self.feedback_widget.add_class("error")
        self.input_widget.add_class("invalid")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Validate and submit the API key."""
        if not (
            event.validation_result
            and event.validation_result.is_valid
            and self.input_widget
            and self.feedback_widget
        ):
            return

        self._reset_feedback()

        self.feedback_widget.update("Validating API key...")
        self.feedback_widget.add_class("info")

        try:
            self._validate_api_key(event.value)
        except ValueError:
            self._show_error("Invalid API key. Please try again.")
            return
        except Exception as err:
            self._show_error(f"Failed to validate API key: {err}")
            return

        self.post_message(self.ApiKeySubmitted(api_key=event.value))

    def on_blur(self, event: events.Blur) -> None:
        """Keep focus on the input widget while active."""
        if self.input_widget:
            self.call_after_refresh(self.input_widget.focus)

    def action_close(self) -> None:
        self.post_message(self.ApiKeyClosed(self.changes.copy()))

    def _reset_feedback(self) -> None:
        assert self.input_widget and self.feedback_widget

        self.feedback_widget.update("")
        self.feedback_widget.remove_class("error", "success", "info")
        self.input_widget.remove_class("valid", "invalid")

    def _show_error(self, message: str) -> None:
        assert self.input_widget and self.feedback_widget

        self.feedback_widget.update(message)
        self.feedback_widget.remove_class("info", "success")
        self.feedback_widget.add_class("error")

        self.input_widget.remove_class("valid")
        self.input_widget.add_class("invalid")
        self.input_widget.focus()

    def _validate_api_key(self, api_key: str) -> None:

        client = Mistral(api_key=api_key)

        try:
            client.models.list()
        except Exception as err:
            raise ValueError("Invalid API key") from err
