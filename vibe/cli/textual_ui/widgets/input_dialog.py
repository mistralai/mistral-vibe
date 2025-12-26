from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import Input, Static

if TYPE_CHECKING:
    pass


class InputDialog(Container):
    """Simple dialog for getting user input."""
    can_focus = True
    can_focus_children = True

    BINDINGS: list[BindingType] = [
        Binding("enter", "submit", "Submit", show=True),
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("1", "create_folder", "Create Folder", show=False),
    ]

    class InputSubmitted(Message):
        """Message sent when input is submitted."""
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    class InputCancelled(Message):
        """Message sent when input is cancelled."""
        def __init__(self) -> None:
            super().__init__()

    class CreateFolder(Message):
        """Message sent when user wants to create a folder."""
        def __init__(self) -> None:
            super().__init__()

    class FolderSelected(Message):
        """Message sent when user selects a folder."""
        def __init__(self, folder_name: str) -> None:
            super().__init__()
            self.folder_name = folder_name

    def __init__(self, title: str, initial_value: str = "", show_folder_option: bool = False, folders: list[str] = None, is_folder_creation: bool = False) -> None:
        super().__init__(id="input-dialog")
        self.title = title
        self.initial_value = initial_value
        self.show_folder_option = show_folder_option
        self.folders = folders or []
        self.is_folder_creation = is_folder_creation
        self.input_widget: Input | None = None
        self.folder_option_widget: Static | None = None
        self.folder_widgets: list[Static] = []
        self.selected_folder_index: int = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="input-content"):
            title = Static(self.title, classes="approval-title")
            yield title

            yield Static("")

            # Show folder creation option if enabled
            if self.show_folder_option:
                self.folder_option_widget = Static(
                    "1. Create Folder",
                    classes="approval-option",
                )
                yield self.folder_option_widget

            # Show available folders if any
            if self.folders:
                yield Static("Folders:", classes="approval-option")
                for folder in self.folders:
                    widget = Static(folder, classes="approval-option")
                    self.folder_widgets.append(widget)
                    yield widget
                yield Static("")

            self.input_widget = Input(
                value=self.initial_value,
                placeholder="Enter name...",
                classes="approval-option",
            )
            yield self.input_widget

            yield Static("")

            help_text = Static(
                "Enter submit  ESC cancel",
                classes="approval-help",
            )
            yield help_text

    def on_mount(self) -> None:
        if self.input_widget:
            self.input_widget.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle when the input widget's enter key is pressed."""
        # Only handle if this is our input widget
        if self.input_widget and event.input == self.input_widget:
            self.action_submit()

    def action_submit(self) -> None:
        """Submit the input value."""
        if self.input_widget:
            value = self.input_widget.value.strip()
            self.post_message(self.InputSubmitted(value=value))

    def action_cancel(self) -> None:
        """Cancel the input."""
        self.post_message(self.InputCancelled())

    def action_create_folder(self) -> None:
        """Create a new folder."""
        if self.show_folder_option:
            self.post_message(self.CreateFolder())

    def on_blur(self, event: events.Blur) -> None:
        """Refocus when blurred."""
        # Don't auto-refocus - let the app handle focus management
        pass
