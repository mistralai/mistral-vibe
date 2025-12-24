from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import Static

if TYPE_CHECKING:
    pass


@dataclass
class ConversationItem:
    """Represents a saved conversation."""
    filepath: Path
    name: str
    date: datetime
    message_count: int
    model: str


class ConversationSelector(Container):
    """Widget for selecting a saved conversation."""
    can_focus = True
    can_focus_children = False

    BINDINGS: list[BindingType] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("escape", "close", "Cancel", show=False),
    ]

    class ConversationSelected(Message):
        """Message sent when a conversation is selected."""
        def __init__(self, filepath: Path) -> None:
            super().__init__()
            self.filepath = filepath

    class ConversationClosed(Message):
        """Message sent when the selector is closed without selection."""
        def __init__(self) -> None:
            super().__init__()

    def __init__(self, conversations: list[ConversationItem]) -> None:
        super().__init__(id="conversation-selector")
        self.conversations = conversations
        self.selected_index = 0
        self.conversation_widgets: list[Static] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="conversation-content"):
            title = Static("Select Conversation", classes="approval-title")
            yield title

            yield Static("")

            for _ in self.conversations:
                widget = Static("", classes="approval-option")
                self.conversation_widgets.append(widget)
                yield widget

            yield Static("")

            help_text = Static(
                "↑↓ navigate  Enter select  ESC cancel",
                classes="approval-help",
            )
            yield help_text

    def on_mount(self) -> None:
        self._update_display()
        self.focus()

    def _update_display(self) -> None:
        """Update the display to show the selected conversation."""
        for i, (conv, widget) in enumerate(
            zip(self.conversations, self.conversation_widgets, strict=True)
        ):
            is_selected = i == self.selected_index
            cursor = "› " if is_selected else "  "

            # Format date nicely
            date_str = conv.date.strftime("%Y-%m-%d %H:%M")

            # Create display text
            text = (
                f"{cursor}{conv.name}\n"
                f"  [blue]{date_str}[/] | "
                f"[green]{conv.message_count} messages[/] | "
                f"[yellow]{conv.model}[/]"
            )

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
        self.selected_index = (self.selected_index - 1) % len(self.conversations)
        self._update_display()

    def action_move_down(self) -> None:
        """Move selection down."""
        self.selected_index = (self.selected_index + 1) % len(self.conversations)
        self._update_display()

    def action_select(self) -> None:
        """Select the currently highlighted conversation."""
        if self.conversations:
            selected_conv = self.conversations[self.selected_index]
            self.post_message(
                self.ConversationSelected(filepath=selected_conv.filepath)
            )

    def action_close(self) -> None:
        """Close the selector without selecting."""
        self.post_message(self.ConversationClosed())

    def on_blur(self, event: events.Blur) -> None:
        """Refocus when blurred."""
        self.call_after_refresh(self.focus)
