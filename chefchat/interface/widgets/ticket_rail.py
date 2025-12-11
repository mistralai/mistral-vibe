"""ChefChat Ticket Rail Widget - The Chat History Panel.

'The Ticket' in kitchen terms is the order slip. This widget displays
the conversation history between the Head Chef (user) and the Brigade (AI).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import TYPE_CHECKING

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import Static

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


from chefchat.interface.constants import MessageType

# Emoji mapping for ticket types
TICKET_EMOJI: dict[MessageType, str] = {
    MessageType.USER: "👨‍🍳",
    MessageType.ASSISTANT: "🍳",
    MessageType.SYSTEM: "📋",
}


@dataclass
class TicketMessage:
    """A single message in the ticket rail."""

    content: str
    ticket_type: MessageType
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        """Set default timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = datetime.now()


class Ticket(Static):
    """A single ticket (message bubble) in the rail."""

    DEFAULT_CSS = """
    Ticket {
        width: 100%;
        margin: 0 0 1 0;
        padding: 1;
        background: $surface;
        border: round $panel-border;
    }

    Ticket.user {
        border-left: thick $accent;
    }

    Ticket.assistant {
        border-left: thick $primary;
    }

    Ticket.system {
        border-left: thick $warning;
        background: $secondary-bg;
    }
    """

    def __init__(
        self,
        content: str,
        ticket_type: MessageType = MessageType.USER,
        timestamp: datetime | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize a ticket.

        Args:
            content: The message content (supports markdown)
            ticket_type: Type of ticket (user/assistant/system)
            timestamp: When the message was sent
            name: Widget name
            id: Widget ID
            classes: CSS classes
        """
        super().__init__(name=name, id=id, classes=classes)
        self.content = content
        self.ticket_type = ticket_type
        self.timestamp = timestamp or datetime.now()

        # Apply CSS class based on type - use value (lower case string)
        self.add_class(ticket_type.value)
        # Add generic ticket class for querying
        self.add_class("ticket")

    def render(self) -> RenderableType:
        """Render the ticket content."""
        time_str = self.timestamp.strftime("%H:%M")

        # Create header with emoji and timestamp
        header = Text()
        emoji = TICKET_EMOJI.get(self.ticket_type, "📋")
        header.append(f"{emoji} ", style="bold")
        header.append(f"[{time_str}]", style="dim")

        # Try to render as markdown, fall back to plain text on specific errors
        try:
            content = Markdown(self.content)
        except (ValueError, TypeError) as e:
            # Specific exceptions for markdown parsing issues
            logger.debug("Markdown rendering failed, using plain text: %s", e)
            content = Text(self.content)
        except Exception as e:
            # Catch-all for unexpected errors, but log them
            logger.warning("Unexpected error rendering markdown: %s", e)
            content = Text(self.content)

        return Group(header, content)


class TicketRail(VerticalScroll):
    """The scrollable chat history container.

    Displays tickets (messages) in chronological order,
    with the most recent at the bottom.
    """

    DEFAULT_CSS = """
    TicketRail {
        background: $secondary-bg;
        border: solid $panel-border;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    TicketRail:focus {
        border: solid $accent;
    }
    """

    BORDER_TITLE = "📋 The Ticket"

    # Reactive list of messages
    messages: reactive[list[TicketMessage]] = reactive(list, init=False)

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the ticket rail.

        Args:
            name: Widget name
            id: Widget ID
            classes: CSS classes
        """
        super().__init__(name=name, id=id, classes=classes)
        self._messages: list[TicketMessage] = []

    def compose(self) -> ComposeResult:
        """Compose the initial empty state."""
        yield Static(
            "[dim italic]Waiting for orders...[/]", id="empty-state", classes="muted"
        )

    def _update_empty_state(self) -> None:
        """Update visibility of empty state message."""
        try:
            empty_state = self.query_one("#empty-state", Static)
            if self._messages:
                empty_state.add_class("hidden")
            else:
                empty_state.remove_class("hidden")
        except NoMatches:
            pass

    def add_ticket(
        self, content: str, ticket_type: MessageType = MessageType.USER
    ) -> Ticket:
        """Add a new ticket to the rail.

        Args:
            content: The message content
            ticket_type: Type of message

        Returns:
            The created Ticket widget
        """
        # Remove empty state on first message via update logic

        message = TicketMessage(content=content, ticket_type=ticket_type)
        self._messages.append(message)

        # Update empty state
        self._update_empty_state()

        ticket = Ticket(
            content=content, ticket_type=ticket_type, timestamp=message.timestamp
        )
        self.mount(ticket)

        # Scroll to bottom
        self.scroll_end(animate=True)

        return ticket

    def add_user_message(self, content: str) -> Ticket:
        """Add a user message (Head Chef's order).

        Args:
            content: The message content

        Returns:
            The created Ticket widget
        """
        return self.add_ticket(content, MessageType.USER)

    def add_assistant_message(self, content: str) -> Ticket:
        """Add an assistant message (Kitchen's response).

        Args:
            content: The message content

        Returns:
            The created Ticket widget
        """
        return self.add_ticket(content, MessageType.ASSISTANT)

    def add_system_message(self, content: str) -> Ticket:
        """Add a system message (Kitchen announcement).

        Args:
            content: The message content

        Returns:
            The created Ticket widget
        """
        return self.add_ticket(content, MessageType.SYSTEM)

    def clear_tickets(self) -> None:
        """Clear all tickets from the rail.

        Properly cleans up both the internal message list and
        all mounted Ticket widgets to prevent memory leaks.
        """
        # Clear internal message list
        self._messages.clear()

        # Batch remove all Ticket widgets using Textual's query batch removal
        self.query(Ticket).remove()

        # Remove empty state if stuck
        try:
            self.query_one("#empty-state").remove()
        except NoMatches:
            pass

        # Restore empty state
        self.mount(
            Static(
                "[dim italic]Waiting for orders...[/]",
                id="empty-state",
                classes="muted",
            )
        )

    def get_message_count(self) -> int:
        """Get the number of messages in the rail.

        Returns:
            Number of messages stored
        """
        return len(self._messages)
