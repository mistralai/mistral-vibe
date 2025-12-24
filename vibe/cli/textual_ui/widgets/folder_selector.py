from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import Static

if TYPE_CHECKING:
    pass


class FolderSelector(Container):
    """Widget for selecting a folder to save conversations to."""

    can_focus = True
    can_focus_children = False

    BINDINGS: list[BindingType] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("escape", "close", "Cancel", show=False),
        Binding("1", "create_folder", "Create Folder", show=False),
    ]

    class FolderSelected(Message):
        """Message sent when a folder is selected."""

        def __init__(self, folder_name: str) -> None:
            super().__init__()
            self.folder_name = folder_name

    class FolderClosed(Message):
        """Message sent when the selector is closed without selection."""

        def __init__(self) -> None:
            super().__init__()

    class CreateFolder(Message):
        """Message sent when user wants to create a folder."""

        def __init__(self) -> None:
            super().__init__()

    def __init__(self, folders: list[str]) -> None:
        super().__init__(id="folder-selector")
        self.folders = folders
        self.selected_index = 0
        self.folder_widgets: list[Static] = []
        self.create_folder_widget: Static | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="folder-content"):
            title = Static("Select Folder", classes="approval-title")
            yield title

            yield Static("")

            # Show "Create Folder" option - NOT in the scrollable list
            self.create_folder_widget = Static(
                "1. Create Folder", classes="approval-option"
            )
            yield self.create_folder_widget

            yield Static("")

            # Show scrollable list of folders
            # Show scrollable list of folders
            # Show "Default (conversations)" option
            widget = Static("Default (conversations)", classes="approval-option")
            self.folder_widgets.append(widget)
            yield widget

            # Show available folders
            for folder in self.folders:
                widget = Static("", classes="approval-option")  # Empty initially, will be formatted in _update_display
                self.folder_widgets.append(widget)
                yield widget

            yield Static("")

            help_text = Static(
                "↑↓ navigate  Enter select  ESC cancel  1=Create New Folder",
                classes="approval-help",
            )
            yield help_text

    def on_mount(self) -> None:
        self._update_display()
        self.focus()

    def _update_display(self) -> None:
        """Update the display to show the selected folder with tree formatting."""
        # Update "Create Folder" widget (always show, not selected)
        if self.create_folder_widget:
            self.create_folder_widget.update("1. Create Folder")
            self.create_folder_widget.remove_class("approval-cursor-selected")
            self.create_folder_widget.add_class("approval-option-selected")

        # Update folder widgets (Default + user folders)
        for i, widget in enumerate(self.folder_widgets):
            is_selected = i == self.selected_index
            cursor = "› " if is_selected else "  "

            # Get the folder name
            if i == 0:
                text = f"{cursor}Default (conversations)"
            else:
                # Only show folders if they exist
                folder_index = (
                    i - 1
                )  # Index 0 is Default, so user folders start at index 1
                if folder_index < len(self.folders):
                    # Add tree-like indentation
                    indent = "  "  # Two spaces for each level
                    tree_marker = "├─ "  # Tree marker
                    text = f"{cursor}{indent}{tree_marker}{self.folders[folder_index]}"
                else:
                    # This shouldn't happen, but handle it gracefully
                    continue

            widget.update(text)

            # Update styling
            widget.remove_class("approval-cursor-selected")
            widget.remove_class("approval-option-selected")

            if is_selected:
                widget.add_class("approval-cursor-selected")
                widget.add_class("approval-option-selected")
            else:
                widget.add_class("approval-option-selected")

    def action_move_up(self) -> None:
        """Move selection up."""
        # We have len(self.folders) + 1 options (Default + user folders)
        total_options = max(1, len(self.folders) + 1)
        # Don't wrap around - stop at first item (Default)
        if self.selected_index > 0:
            self.selected_index -= 1
        self._update_display()

    def action_move_down(self) -> None:
        """Move selection down."""
        # We have len(self.folders) + 1 options (Default + user folders)
        total_options = max(1, len(self.folders) + 1)
        # Wrap around at the end
        self.selected_index = (self.selected_index + 1) % total_options
        self._update_display()

    def action_select(self) -> None:
        """Select the currently highlighted folder."""
        # Get the selected folder name
        if self.selected_index == 0:
            # Default option
            folder_name = ""  # Empty means save to root conversations directory
            self.post_message(self.FolderSelected(folder_name=folder_name))
        else:
            # Folder from the list
            folder_index = (
                self.selected_index - 1
            )  # Index 0 is Default, rest are user folders
            if folder_index < len(self.folders):
                folder_name = self.folders[folder_index]
                self.post_message(self.FolderSelected(folder_name=folder_name))

    def action_close(self) -> None:
        """Close the selector without selecting."""
        self.post_message(self.FolderClosed())

    async def action_create_folder(self) -> None:
        """Show input for creating a new folder."""
        # Post message to let the app know user wants to create a folder
        self.post_message(self.CreateFolder())

    def on_blur(self, event: events.Blur) -> None:
        """Refocus when blurred."""
        self.call_after_refresh(self.focus)
