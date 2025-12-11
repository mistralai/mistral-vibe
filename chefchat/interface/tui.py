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
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, ClassVar
from uuid import uuid4

from textual import on
from textual.app import App, ComposeResult
from textual.worker import Worker
from textual.binding import Binding
from textual.containers import Grid, Horizontal
from textual.widgets import Footer, Input, Static

from chefchat.interface.constants import (
    MARKDOWN_SANITIZE_CHARS,
    WHISK_FRAMES,
    BusAction,
    PayloadKey,
    StationStatus,
    StatusString,
    TicketCommand,
)
from chefchat.interface.widgets.the_pass import ThePass
from chefchat.interface.widgets.the_plate import ThePlate
from chefchat.interface.widgets.ticket_rail import TicketRail
from chefchat.interface.screens.command_palette import CommandPalette
from chefchat.kitchen.brigade import Brigade, create_default_brigade
from chefchat.kitchen.bus import ChefMessage, KitchenBus, MessagePriority

if TYPE_CHECKING:
    pass

# Configure logging for TUI module
logger = logging.getLogger(__name__)


class WhiskLoader(Horizontal):
    """Animated loading indicator with kitchen flair.

    Uses proper async task management to avoid race conditions
    when starting and stopping the animation.
    """

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
        self._message = "Cooking..."
        self._worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        """Compose the loader."""
        yield Static(WHISK_FRAMES[0], classes="whisk-spinner")
        yield Static(self._message, classes="whisk-message")

    async def on_unmount(self) -> None:
        """Ensure animation worker is stopped when widget is unmounted."""
        self.stop()

    def start(self, message: str = "Cooking...") -> None:
        """Start the loading animation.

        Args:
            message: Status message to display
        """
        # Update message regardless of running state
        self._message = message
        try:
            msg_widget = self.query_one(".whisk-message", Static)
            msg_widget.update(message)
        except Exception:
            # Widget may not be composed yet; safe to ignore
            pass

        # If already running, we're done
        if self._worker and not self._worker.is_finished:
            return

        self.add_class("visible")
        self._worker = self.run_worker(
            self._animate(), exclusive=True, group="whisk_animation", exit_on_error=False
        )

    async def _animate(self) -> None:
        """Animation loop for the whisk using Textual worker.

        Runs until cancelled.
        """
        try:
            while True:
                self._frame_index = (self._frame_index + 1) % len(WHISK_FRAMES)

                try:
                    spinner = self.query_one(".whisk-spinner", Static)
                    spinner.update(WHISK_FRAMES[self._frame_index])
                except Exception:
                    # Widget not available, stop animation
                    break

                await asyncio.sleep(0.15)
        except asyncio.CancelledError:
            # Clean exit on cancellation
            pass
        finally:
            # Ensure visual state is cleared on exit
            self.remove_class("visible")

    def stop(self) -> None:
        """Stop the loading animation.

        Explicitly cancels the animation worker.
        """
        if self._worker:
            self._worker.cancel()
            self._worker = None
        self.remove_class("visible")
        self._frame_index = 0


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


def sanitize_markdown_input(text: str) -> str:
    """Sanitize user input for safe markdown rendering.

    Removes potentially dangerous control characters, ANSI codes,
    and partial markup that could break Textual's rendering.

    Args:
        text: Raw user input

    Returns:
        Sanitized text safe for markdown rendering
    """
    if not text:
        return ""

    sanitized = text

    # Remove dangerous control characters using the map
    for char, replacement in MARKDOWN_SANITIZE_CHARS.items():
        sanitized = sanitized.replace(char, replacement)

    # Remove ANSI escape sequences
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    sanitized = ansi_escape.sub("", sanitized)

    # Additional cleanup for Textual specific issues if needed
    # For now ensuring no null bytes which definitely break things
    sanitized = sanitized.replace("\0", "")

    return sanitized


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

    # Status string to StationStatus mapping
    _STATUS_MAP: ClassVar[dict[str, StationStatus]] = {
        StatusString.IDLE.value: StationStatus.IDLE,
        StatusString.PLANNING.value: StationStatus.WORKING,
        StatusString.COOKING.value: StationStatus.WORKING,
        StatusString.TESTING.value: StationStatus.WORKING,
        StatusString.REFACTORING.value: StationStatus.WORKING,
        StatusString.COMPLETE.value: StationStatus.COMPLETE,
        StatusString.ERROR.value: StationStatus.ERROR,
    }

    def __init__(self) -> None:
        """Initialize the ChefChat TUI."""
        super().__init__()
        self._bus: KitchenBus | None = None
        self._brigade: Brigade | None = None
        self._processing = False
        self._stations_started = False

    @property
    def bus(self) -> KitchenBus:
        """Get the kitchen bus, raising if not initialized."""
        if self._bus is None:
            raise RuntimeError("Kitchen bus not initialized")
        return self._bus

    def compose(self) -> ComposeResult:
        """Compose the 3-pane kitchen layout."""
        try:
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
        except Exception as e:
            logger.exception("Error in compose: %s", e)
            raise

    async def on_mount(self) -> None:
        """Handle app mount - start workers and show welcome."""
        try:
            ticket_rail = self.query_one("#ticket-rail", TicketRail)
            ticket_rail.add_system_message(
                "🍽️ **Welcome to ChefChat!**\n\n"
                "The kitchen is ready, Chef. What would you like to cook today?\n\n"
                "*Commands: `/help` for menu, `/clear` to reset*"
            )

            # Focus the input
            self.query_one("#command-input", CommandInput).focus()

            # Build brigade and start the kitchen
            await self._setup_brigade()

        except Exception as e:
            logger.exception("Error in on_mount: %s", e)
            raise

    async def on_unmount(self) -> None:
        """Ensure background resources are cleaned up when the app unmounts."""
        await self._shutdown()

    async def _handle_bus_message(self, message: ChefMessage) -> None:
        """Handle incoming messages from the bus.

        This is the TUI's subscription callback. It routes
        messages to the appropriate UI widgets.

        Args:
            message: The message from the bus
        """
        try:
            match message.action:
                case BusAction.STATUS_UPDATE.value:
                    await self._update_station_status(message.payload)
                case BusAction.LOG_MESSAGE.value:
                    await self._add_log_message(message.payload)
                case BusAction.PLATE_CODE.value:
                    await self._plate_code(message.payload)
                case BusAction.STREAM_UPDATE.value:
                    await self._plate_code(message.payload, append=True)
                case BusAction.TERMINAL_LOG.value:
                    await self._add_terminal_log(message.payload)
                case BusAction.PLAN.value:
                    await self._add_plan(message.payload)
                case _:
                    logger.debug("Unhandled bus action: %s", message.action)
        except Exception as exc:
            logger.exception("Error handling bus message: %s", exc)

    async def _update_station_status(self, payload: dict) -> None:
        station_id = payload.get(PayloadKey.STATION, "")
        if not station_id:
            return

        status_raw = str(payload.get(PayloadKey.STATUS, "")).lower()
        status = self._STATUS_MAP.get(status_raw, StationStatus.IDLE)
        progress = float(payload.get(PayloadKey.PROGRESS, 0.0) or 0.0)
        message = str(payload.get(PayloadKey.MESSAGE, "")) or status.name.capitalize()

        station_board = self.query_one("#the-pass", ThePass)
        station_board.update_station(station_id, status, progress, message)

    async def _add_log_message(self, payload: dict) -> None:
        content = str(payload.get(PayloadKey.CONTENT, "")) or str(
            payload.get(PayloadKey.MESSAGE, "")
        )
        if not content:
            return

        ticket_rail = self.query_one("#ticket-rail", TicketRail)
        ticket_rail.add_assistant_message(content)

        plate = self.query_one("#the-plate", ThePlate)
        plate.log_message(content)

    async def _plate_code(self, payload: dict, *, append: bool = False) -> None:
        code = str(payload.get(PayloadKey.CODE, ""))
        if not code:
            return

        language = str(payload.get(PayloadKey.LANGUAGE, "python")) or "python"
        file_path = payload.get(PayloadKey.FILE_PATH)
        plate = self.query_one("#the-plate", ThePlate)
        plate.plate_code(code, language=language, file_path=file_path, append=append)

    async def _add_terminal_log(self, payload: dict) -> None:
        message = str(payload.get(PayloadKey.MESSAGE, "")) or str(
            payload.get(PayloadKey.CONTENT, "")
        )
        if not message:
            return
        plate = self.query_one("#the-plate", ThePlate)
        plate.log_message(message)

    async def _add_plan(self, payload: dict) -> None:
        task = str(payload.get(PayloadKey.TASK, "")) or str(
            payload.get(PayloadKey.CONTENT, "")
        )
        if not task:
            return
        ticket_rail = self.query_one("#ticket-rail", TicketRail)
        ticket_rail.add_system_message(f"🗺️ Plan updated: {task}")

    @on(Input.Submitted)
    async def handle_input(self, event: Input.Submitted) -> None:
        """Handle user input submission."""
        if not event.value.strip():
            return

        user_input = sanitize_markdown_input(event.value.strip())
        if not user_input.strip():
            return

        # Check for slash commands
        if user_input.startswith("/"):
            await self._handle_command(user_input)
            return

        # Normal chat message
        await self._submit_ticket(user_input)

    async def _submit_ticket(self, request: str) -> None:
        """Submit a new ticket to the kitchen via the bus.

        Args:
            request: The user's request
        """
        ticket_id = str(uuid4())[:8]

        if not self._bus:
            return

        message = ChefMessage(
            sender="tui",
            recipient="sous_chef",
            action=BusAction.NEW_TICKET.value,
            payload={PayloadKey.TICKET_ID: ticket_id, PayloadKey.REQUEST: request},
            priority=MessagePriority.HIGH,
        )

        await self._bus.publish(message)

    async def _handle_command(self, command: str) -> None:
        if not command.startswith("/"):
            return

        parts = command.lstrip("/").split(maxsplit=1)
        name = parts[0].strip().lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        match name:
            case TicketCommand.HELP.value:
                await self._show_command_palette()
            case TicketCommand.CLEAR.value:
                await self._handle_clear()
            case TicketCommand.QUIT.value:
                await self._shutdown()
                self.exit()
            case TicketCommand.CHEF.value:
                await self._submit_ticket(arg or "chef: plan this task")
            case TicketCommand.PLATE.value:
                plate = self.query_one("#the-plate", ThePlate)
                plate.show_current_plate()
            case _:
                ticket_rail = self.query_one("#ticket-rail", TicketRail)
                ticket_rail.add_system_message(f"Unknown command: /{name}")

    async def _show_command_palette(self) -> None:
        palette = CommandPalette()
        self.push_screen(palette)

    async def _handle_clear(self) -> None:
        ticket_rail = self.query_one("#ticket-rail", TicketRail)
        ticket_rail.clear_tickets()
        plate = self.query_one("#the-plate", ThePlate)
        plate.clear_plate()
        station_board = self.query_one("#the-pass", ThePass)
        station_board.reset_all()

    async def _shutdown(self) -> None:
        """Gracefully shutdown the kitchen."""
        if self._brigade:
            await self._brigade.close_kitchen()
        elif self._bus:
            await self._bus.stop()

    def action_quit(self) -> None:
        """Quit the application."""
        if self._brigade or self._bus:
            self.call_after_refresh(lambda: asyncio.create_task(self._shutdown()))
        self.exit()

    def action_cancel(self) -> None:
        """Cancel current processing and hide loader."""
        self._processing = False
        try:
            self.query_one(WhiskLoader).stop()
        except Exception:
            pass

    def action_clear(self) -> None:
        """Clear tickets, plate, and reset stations."""
        asyncio.create_task(self._handle_clear())

    def action_focus_input(self) -> None:
        """Focus the command input."""
        self.query_one("#command-input", CommandInput).focus()

    async def _setup_brigade(self) -> None:
        """Create and start the full brigade and bus."""
        self._brigade = await create_default_brigade()
        self._bus = self._brigade.bus

        # Subscribe TUI to bus messages
        self._bus.subscribe("tui", self._handle_bus_message)

        # Open kitchen (starts bus and stations)
        await self._brigade.open_kitchen()
        self._stations_started = True


def run(*, verbose: bool = False) -> None:
    """Run the ChefChat TUI application."""
    import os
    import sys
    import logging
    import traceback
    
    # Force Textual to work with color
    os.environ.setdefault("FORCE_COLOR", "1")

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("TUI requires an interactive TTY (stdin/stdout must be a terminal)")

    term = (os.environ.get("TERM") or "").strip().lower()
    if not term or term in {"dumb", "unknown"}:
        os.environ["TERM"] = "xterm-256color"
    
    app = ChefChatApp()
    
    # Run with proper exception handling
    try:
        app.run()
    except KeyboardInterrupt:
        print("\n👋 Shutting down kitchen...")
        return
    except Exception as e:
        if verbose:
            logging.basicConfig(level=logging.DEBUG)
        print(f"\n❌ Failed to start TUI: {e}", file=sys.stderr)
        if verbose:
            traceback.print_exc()
        raise


if __name__ == "__main__":
    run()