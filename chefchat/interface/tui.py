"""ChefChat TUI - The Main Textual Application.

This is the culinary-themed Terminal User Interface for ChefChat.
The 3-pane layout represents a professional kitchen:
- The Ticket (left): Where orders (chat) come in
- The Pass (right): Where the brigade reports status
- The Plate (bottom): Where the finished dish (code) is presented

This version integrates with the async KitchenBus to receive
real-time updates from the kitchen stations (agents).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar
from uuid import uuid4

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal
from textual.widgets import Footer, Input, Static

from chefchat.interface.widgets.the_pass import StationStatus, ThePass
from chefchat.interface.widgets.the_plate import ThePlate
from chefchat.interface.widgets.ticket_rail import TicketRail
from chefchat.kitchen.bus import ChefMessage, KitchenBus, MessagePriority

if TYPE_CHECKING:
    pass


# Whisk animation frames - ASCII art spinner
WHISK_FRAMES = ["   🥄", "  🥄 ", " 🥄  ", "🥄   ", " 🥄  ", "  🥄 "]


class WhiskLoader(Horizontal):
    """Animated loading indicator with kitchen flair."""

    DEFAULT_CSS = """
    WhiskLoader {
        dock: bottom;
        height: 3;
        background: $surface;
        border-top: solid $panel-border;
        padding: 0 2;
        align: center middle;
        display: none;
    }

    WhiskLoader.visible {
        display: block;
    }

    WhiskLoader .whisk-spinner {
        color: $accent;
        text-style: bold;
        width: 6;
    }

    WhiskLoader .whisk-message {
        color: $text-muted;
        margin-left: 2;
    }
    """

    def __init__(self) -> None:
        """Initialize the whisk loader."""
        super().__init__()
        self._frame_index = 0
        self._running = False
        self._message = "Cooking..."

    def compose(self) -> ComposeResult:
        """Compose the loader."""
        yield Static(WHISK_FRAMES[0], classes="whisk-spinner")
        yield Static(self._message, classes="whisk-message")

    def start(self, message: str = "Cooking...") -> None:
        """Start the loading animation.

        Args:
            message: Status message to display
        """
        self._message = message
        self._running = True
        msg_widget = self.query_one(".whisk-message", Static)
        msg_widget.update(message)
        self.add_class("visible")
        self._animate()

    @work(exclusive=True, name="whisk_animation")
    async def _animate(self) -> None:
        """Animation loop for the whisk using Textual worker."""
        while self._running:
            self._frame_index = (self._frame_index + 1) % len(WHISK_FRAMES)
            spinner = self.query_one(".whisk-spinner", Static)
            spinner.update(WHISK_FRAMES[self._frame_index])
            await asyncio.sleep(0.15)

    def stop(self) -> None:
        """Stop the loading animation."""
        self._running = False
        self.remove_class("visible")


class KitchenHeader(Static):
    """Custom header with kitchen branding."""

    DEFAULT_CSS = """
    KitchenHeader {
        dock: top;
        height: 3;
        background: $surface;
        border-bottom: solid $accent;
        padding: 0 2;
        content-align: center middle;
    }

    KitchenHeader .header-title {
        color: $accent;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        """Compose the header."""
        yield Static(
            "👨‍🍳 [bold]ChefChat[/] • The Michelin Star AI-Engineer",
            classes="header-title",
        )


class CommandInput(Input):
    """Custom command input with kitchen styling."""

    DEFAULT_CSS = """
    CommandInput {
        dock: bottom;
        height: 3;
        background: $primary-bg;
        border: solid $panel-border;
        padding: 0 1;
    }

    CommandInput:focus {
        border: solid $accent;
    }
    """


class ChefChatApp(App):
    """The main ChefChat TUI application.

    A 3-pane layout representing a professional kitchen:
    - The Ticket (orders/chat)
    - The Pass (brigade status)
    - The Plate (code output)

    Integrates with KitchenBus for async communication with agents.
    """

    # Load custom CSS
    CSS_PATH = Path(__file__).parent / "styles.tcss"

    TITLE = "ChefChat"
    SUB_TITLE = "The Michelin Star AI-Engineer"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+c", "cancel", "Cancel", show=True),
        Binding("ctrl+l", "clear", "Clear Plate", show=True),
        Binding("escape", "focus_input", "Focus Input", show=False),
    ]

    def __init__(self) -> None:
        """Initialize the ChefChat TUI."""
        super().__init__()
        self._bus = KitchenBus()
        self._processing = False
        self._stations_started = False

    def compose(self) -> ComposeResult:
        """Compose the 3-pane kitchen layout."""
        yield KitchenHeader()

        with Grid(id="kitchen-grid"):
            yield TicketRail(id="ticket-rail")
            yield ThePass(id="the-pass")
            yield ThePlate(id="the-plate")

        yield WhiskLoader()
        yield CommandInput(
            placeholder="🍳 What shall we cook today, Chef?", id="command-input"
        )
        yield Footer()

    async def on_mount(self) -> None:
        """Handle app mount - start workers and show welcome."""
        ticket_rail = self.query_one("#ticket-rail", TicketRail)
        ticket_rail.add_system_message(
            "🍽️ **Welcome to ChefChat!**\n\n"
            "The kitchen is ready, Chef. What would you like to cook today?\n\n"
            "*Commands: `/help` for menu, `/clear` to reset*"
        )

        # Focus the input
        self.query_one("#command-input", CommandInput).focus()

        # Subscribe TUI to bus messages
        self._bus.subscribe("tui", self._handle_bus_message)

        # Start the bus
        await self._bus.start()

        # Start the kitchen stations as workers
        self._start_kitchen_workers()

    @work(exclusive=False, name="kitchen_stations")
    async def _start_kitchen_workers(self) -> None:
        """Start the kitchen station workers."""
        from chefchat.kitchen.stations.expeditor import Expeditor
        from chefchat.kitchen.stations.line_cook import LineCook
        from chefchat.kitchen.stations.sous_chef import SousChef

        # Create and start stations
        sous_chef = SousChef(self._bus)
        line_cook = LineCook(self._bus)
        expeditor = Expeditor(self._bus)

        # Start their listening loops
        await asyncio.gather(sous_chef.start(), line_cook.start(), expeditor.start())

    async def _handle_bus_message(self, message: ChefMessage) -> None:
        """Handle incoming messages from the bus.

        This is the TUI's subscription callback. It routes
        messages to the appropriate UI widgets.

        Args:
            message: The message from the bus
        """
        action = message.action
        payload = message.payload

        if action == "STATUS_UPDATE":
            # Update station progress in The Pass
            await self._update_station_status(payload)

        elif action == "LOG_MESSAGE":
            # Add message to The Ticket
            await self._add_log_message(payload)

        elif action == "PLATE_CODE":
            # Display code on The Plate
            await self._plate_code(payload)

    async def _update_station_status(self, payload: dict) -> None:
        """Update a station's status in The Pass.

        Args:
            payload: Status update data
        """
        station = payload.get("station", "")
        status_str = payload.get("status", "idle")
        progress = payload.get("progress", 0)
        message_text = payload.get("message", "")

        # Map status string to enum
        status_map = {
            "idle": StationStatus.IDLE,
            "planning": StationStatus.WORKING,
            "cooking": StationStatus.WORKING,
            "testing": StationStatus.WORKING,
            "refactoring": StationStatus.WORKING,
            "complete": StationStatus.COMPLETE,
            "error": StationStatus.ERROR,
        }
        status = status_map.get(status_str, StationStatus.IDLE)

        # Update The Pass
        the_pass = self.query_one("#the-pass", ThePass)
        the_pass.update_station(station, status, float(progress), message_text)

        # Start/stop whisk loader based on status
        loader = self.query_one(WhiskLoader)
        if status == StationStatus.WORKING:
            if not self._processing:
                self._processing = True
                loader.start(message_text)
        elif status == StationStatus.COMPLETE and station == "line_cook":
            self._processing = False
            loader.stop()
            # Reset all stations after completion
            await asyncio.sleep(1)
            the_pass.reset_all()

    async def _add_log_message(self, payload: dict) -> None:
        """Add a message to The Ticket rail.

        Args:
            payload: Message data
        """
        msg_type = payload.get("type", "system")
        content = payload.get("content", "")

        ticket_rail = self.query_one("#ticket-rail", TicketRail)

        if msg_type == "user":
            ticket_rail.add_user_message(content)
        elif msg_type == "assistant":
            ticket_rail.add_assistant_message(content)
        else:
            ticket_rail.add_system_message(content)

    async def _plate_code(self, payload: dict) -> None:
        """Display code on The Plate.

        Args:
            payload: Code data
        """
        code = payload.get("code", "")
        language = payload.get("language", "python")
        file_path = payload.get("file_path", None)

        plate = self.query_one("#the-plate", ThePlate)
        plate.plate_code(code, language, file_path)

    @on(Input.Submitted)
    async def handle_input(self, event: Input.Submitted) -> None:
        """Handle user input submission."""
        if not event.value.strip():
            return

        user_input = event.value.strip()
        event.input.clear()

        # Handle slash commands
        if user_input.startswith("/"):
            await self._handle_command(user_input)
            return

        # Add user message to ticket rail
        ticket_rail = self.query_one("#ticket-rail", TicketRail)
        ticket_rail.add_user_message(user_input)

        # Send NEW_TICKET to the bus
        await self._submit_ticket(user_input)

    async def _submit_ticket(self, request: str) -> None:
        """Submit a new ticket to the kitchen via the bus.

        Args:
            request: The user's request
        """
        ticket_id = str(uuid4())[:8]

        message = ChefMessage(
            sender="tui",
            recipient="sous_chef",
            action="NEW_TICKET",
            payload={"ticket_id": ticket_id, "request": request},
            priority=MessagePriority.HIGH,
        )

        await self._bus.publish(message)

    async def _handle_command(self, command: str) -> None:
        """Handle slash commands.

        Args:
            command: The command string (e.g., '/help')
        """
        ticket_rail = self.query_one("#ticket-rail", TicketRail)

        cmd = command.lower().split()[0]

        if cmd == "/help":
            ticket_rail.add_system_message(
                "📋 **Kitchen Menu**\n\n"
                "**Basic Commands:**\n"
                "- `/help` - Show this menu\n"
                "- `/clear` - Clear the conversation\n"
                "- `/plate` - Clear the code plate\n"
                "- `/status` - Show brigade status\n"
                "- `/quit` - Close the kitchen\n\n"
                "**Chef Commands:**\n"
                "- `/chef prep` - Scan codebase into Knowledge Graph\n"
                "- `/chef cook <recipe>` - Execute a recipe\n"
                "- `/chef taste [path]` - Run tests & linter (QA)\n"
                "- `/chef undo` - Undo last ChefChat changes\n"
                "- `/chef critic <file>` - Get a roast of your code 🔥\n"
                "- `/chef recipes` - List available recipes"
            )

        elif cmd == "/clear":
            ticket_rail.clear_tickets()
            ticket_rail.add_system_message("🧹 Kitchen cleared!")

        elif cmd == "/plate":
            plate = self.query_one("#the-plate", ThePlate)
            plate.clear_plate()
            ticket_rail.add_system_message("🍽️ Plate cleared!")

        elif cmd == "/status":
            ticket_rail.add_system_message(
                "👨‍🍳 **Brigade Status**\n\nAll stations standing by."
            )

        elif cmd == "/quit":
            await self._shutdown()
            self.exit()

        elif cmd == "/chef":
            # Route /chef commands via bus to SousChef
            ticket_rail.add_user_message(command)
            await self._submit_ticket(command)

        else:
            ticket_rail.add_system_message(
                f"❓ Unknown command: `{cmd}`\nType `/help` for available commands."
            )

    async def _shutdown(self) -> None:
        """Gracefully shutdown the kitchen."""
        await self._bus.stop()

    def action_quit(self) -> None:
        """Quit the application."""
        self.exit()

    def action_cancel(self) -> None:
        """Cancel current operation."""
        if self._processing:
            loader = self.query_one(WhiskLoader)
            loader.stop()
            self._processing = False

            ticket_rail = self.query_one("#ticket-rail", TicketRail)
            ticket_rail.add_system_message("⚠️ Operation cancelled.")

            the_pass = self.query_one("#the-pass", ThePass)
            the_pass.reset_all()

    def action_clear(self) -> None:
        """Clear the plate."""
        plate = self.query_one("#the-plate", ThePlate)
        plate.clear_plate()

    def action_focus_input(self) -> None:
        """Focus the command input."""
        self.query_one("#command-input", CommandInput).focus()


def run() -> None:
    """Run the ChefChat TUI application."""
    app = ChefChatApp()
    app.run()


if __name__ == "__main__":
    run()
