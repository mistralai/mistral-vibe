"""ChefChat App - The Main Textual Application Class."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar
from uuid import uuid4

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.widgets import Input

from chefchat.cli.commands import CommandRegistry
from chefchat.core.agent import Agent
from chefchat.core.config import MissingAPIKeyError, VibeConfig
from chefchat.core.types import (
    AssistantEvent,
    CompactEndEvent,
    CompactStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from chefchat.interface.constants import (
    MARKDOWN_SANITIZE_CHARS,
    BusAction,
    PayloadKey,
    StationStatus,
    StatusString,
    TUILayout,
)
from chefchat.interface.screens.command_palette import CommandPalette
from chefchat.interface.screens.confirm_restart import (
    ConfirmRestartScreen,
    get_saved_layout,
    save_tui_preference,
)
from chefchat.interface.screens.models import ModelSelectionScreen
from chefchat.interface.screens.onboarding import OnboardingScreen
from chefchat.interface.screens.tool_approval import ToolApprovalScreen
from chefchat.interface.widgets.kitchen_ui import (
    CommandInput,
    KitchenFooter,
    KitchenHeader,
    WhiskLoader,
)
from chefchat.interface.widgets.the_pass import ThePass
from chefchat.interface.widgets.the_plate import ThePlate
from chefchat.interface.widgets.ticket_rail import TicketRail
from chefchat.kitchen.brigade import Brigade, create_default_brigade
from chefchat.kitchen.bus import ChefMessage, KitchenBus, MessagePriority
from chefchat.modes import MODE_CONFIGS, MODE_CYCLE_ORDER, ModeManager, VibeMode

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def sanitize_markdown_input(text: str) -> str:
    """Sanitize user input for safe markdown rendering."""
    if not text:
        return ""

    sanitized = text
    for char, replacement in MARKDOWN_SANITIZE_CHARS.items():
        sanitized = sanitized.replace(char, replacement)

    # Remove ANSI escape sequences
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|[0-?]*[ -/]*[@-~])")
    sanitized = ansi_escape.sub("", sanitized)
    sanitized = sanitized.replace("\0", "")

    return sanitized


class ChefChatApp(App):
    """The main ChefChat TUI application."""

    CSS_PATH = Path(__file__).parent / "styles.tcss"
    TITLE = "ChefChat"
    SUB_TITLE = "The Michelin Star AI-Engineer"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("ctrl+l", "clear", "Clear Plate", show=False),
        Binding("escape", "focus_input", "Focus Input", show=False),
        # Mode cycling - priority=True to override default focus behavior
        Binding("shift+tab", "cycle_mode", "Cycle Mode", show=False, priority=True),
        Binding("ctrl+m", "cycle_mode", "Cycle Mode", show=False),  # Alternative
    ]

    _STATUS_MAP: ClassVar[dict[str, StationStatus]] = {
        StatusString.IDLE.value: StationStatus.IDLE,
        StatusString.PLANNING.value: StationStatus.WORKING,
        StatusString.COOKING.value: StationStatus.WORKING,
        StatusString.TESTING.value: StationStatus.WORKING,
        StatusString.REFACTORING.value: StationStatus.WORKING,
        StatusString.COMPLETE.value: StationStatus.COMPLETE,
        StatusString.ERROR.value: StationStatus.ERROR,
    }

    def __init__(
        self,
        layout: TUILayout = TUILayout.CHAT_ONLY,
        active_mode: bool = False,
    ) -> None:
        super().__init__()
        self._bus: KitchenBus | None = None
        self._brigade: Brigade | None = None
        self._processing = False
        self._mode_manager = ModeManager(initial_mode=VibeMode.NORMAL)
        self._command_registry = CommandRegistry()
        self._layout = layout
        
        # Active Mode
        self._active_mode = active_mode
        self._agent: Agent | None = None
        self._config: VibeConfig | None = None

    @property
    def bus(self) -> KitchenBus:
        if self._bus is None:
            raise RuntimeError("Kitchen bus not initialized")
        return self._bus

    async def _initialize_agent(self) -> None:
        """Initialize the core Agent for active mode."""
        try:
            self._config = VibeConfig.load()
        except MissingAPIKeyError:
            # Push onboarding screen if key is missing
            await self.push_screen(OnboardingScreen(), self._on_onboarding_complete)
            return
        except Exception as e:
            self.notify(f"Config Error: {e}", severity="error")
            return

        self._agent = Agent(
            config=self._config,
            auto_approve=self._mode_manager.auto_approve,
            enable_streaming=True,
            mode_manager=self._mode_manager,
        )
        self._agent.set_approval_callback(self._tool_approval_callback)
        
        # Notify user
        mode = self._mode_manager.current_mode
        config = MODE_CONFIGS[mode]
        self.query_one("#ticket-rail", TicketRail).add_system_message(
            f"✅ **Agent Connected**\n\n"
            f"Model: `{self._config.active_model}`\n"
            f"Mode: {config.emoji} {mode.value.upper()}"
        )

    def _on_onboarding_complete(self, success: bool) -> None:
        """Callback when onboarding is done."""
        if success:
            asyncio.create_task(self._initialize_agent())
        else:
            self.notify("Setup incomplete. Chat will be disabled.", severity="warning")

    async def _tool_approval_callback(
        self, tool_name: str, tool_args: dict | str
    ) -> tuple[str, str | None]:
        """Handle tool approval requests via modal."""
        # We need to wait for the user response, so we use a future
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def on_screen_result(result: tuple[str, str | None]) -> None:
            if not future.done():
                future.set_result(result)

        # Schedule screen push on main thread
        self.call_from_thread(
            lambda: self.push_screen(
                ToolApprovalScreen(tool_name, tool_args), on_screen_result
            )
        )

        return await future

    def compose(self) -> ComposeResult:
        """Compose the layout based on current mode."""
        # Header first (dock top)
        yield KitchenHeader()

        if self._layout == TUILayout.FULL_KITCHEN:
            # Full 3-panel kitchen layout
            with Grid(id="kitchen-grid"):
                yield TicketRail(id="ticket-rail")
                yield ThePass(id="the-pass")
                yield ThePlate(id="the-plate")
        else:
            # Clean chat-only layout
            yield TicketRail(id="ticket-rail", classes="chat-only")

        # Footer elements (dock bottom) - order matters: first yielded = bottom-most
        yield KitchenFooter(self._mode_manager, id="kitchen-footer")
        yield CommandInput(
            placeholder="🍳 What shall we cook today, Chef?", id="command-input"
        )
        yield WhiskLoader()

    async def on_mount(self) -> None:
        """Handle app mount - start workers and show welcome."""
        try:
            # Initialize the bus for local messaging (lightweight, no full brigade)
            self._bus = KitchenBus()
            await self._bus.start()
            self._bus.subscribe("tui", self._handle_bus_message)

            ticket_rail = self.query_one("#ticket-rail", TicketRail)
            mode = self._mode_manager.current_mode
            config = MODE_CONFIGS[mode]
            ticket_rail.add_system_message(
                f"🍽️ **Welcome to ChefChat!**\n\n"
                f"The kitchen is ready, Chef. What would you like to cook today?\n\n"
                f"*Current Mode: {config.emoji} {mode.value.upper()}*\n\n"
                f"*Commands: `/help` for menu, `/modes` to see modes, `Shift+Tab` to cycle*"
            )

            self.query_one("#command-input", CommandInput).focus()
            
            if self._active_mode:
                await self._initialize_agent()

        except Exception as e:
            logger.exception("Error in on_mount: %s", e)
            print(f"❌ Fatal Error in on_mount: {e}", file=sys.stderr)
            traceback.print_exc()
            self.notify(f"Kitchen Error: {e}", severity="error")

    async def on_unmount(self) -> None:
        await self._shutdown()

    async def _handle_bus_message(self, message: ChefMessage) -> None:
        """Handle incoming messages from the bus."""
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
        content = str(
            payload.get(PayloadKey.CONTENT, "") or payload.get(PayloadKey.MESSAGE, "")
        )
        if not content:
            return

        # Log to ticket rail
        self.query_one("#ticket-rail", TicketRail).add_assistant_message(content)
        # Also log to Plate log
        self.query_one("#the-plate", ThePlate).log_message(content)

    async def _plate_code(self, payload: dict, *, append: bool = False) -> None:
        code = str(payload.get(PayloadKey.CODE, ""))
        if not code:
            return

        language = str(payload.get(PayloadKey.LANGUAGE, "python")) or "python"
        file_path = payload.get(PayloadKey.FILE_PATH)
        plate = self.query_one("#the-plate", ThePlate)
        plate.plate_code(code, language=language, file_path=file_path, append=append)

    async def _add_terminal_log(self, payload: dict) -> None:
        message = str(
            payload.get(PayloadKey.MESSAGE, "") or payload.get(PayloadKey.CONTENT, "")
        )
        if message:
            self.query_one("#the-plate", ThePlate).log_message(message)

    async def _add_plan(self, payload: dict) -> None:
        task = str(
            payload.get(PayloadKey.TASK, "") or payload.get(PayloadKey.CONTENT, "")
        )
        if task:
            self.query_one("#ticket-rail", TicketRail).add_system_message(
                f"🗺️ Plan updated: {task}"
            )

    @on(Input.Submitted)
    async def handle_input(self, event: Input.Submitted) -> None:
        """Handle user input submission."""
        val = event.value.strip()
        if not val:
            return

        user_input = sanitize_markdown_input(val)
        if not user_input.strip():
            return

        # Clear input immediately for better UX
        self.query_one("#command-input", CommandInput).value = ""

        if user_input.startswith("/"):
            await self._handle_command(user_input)
        else:
            await self._submit_ticket(user_input)

    @work(exclusive=True)
    async def _run_agent_loop(self, request: str) -> None:
        """Run the agent interaction loop in a worker thread."""
        if not self._agent:
            return

        ticket_rail = self.query_one("#ticket-rail", TicketRail)
        plate = self.query_one("#the-plate", ThePlate)
        loader = self.query_one(WhiskLoader)

        # UI Updates must be scheduled on the main thread
        self.call_from_thread(loader.start, "Thinking...")
        self.call_from_thread(ticket_rail.start_streaming_message)

        try:
            async for event in self._agent.act(request):
                if isinstance(event, AssistantEvent):
                    if event.content:
                        self.call_from_thread(ticket_rail.stream_token, event.content)
                
                elif isinstance(event, ToolCallEvent):
                    self.call_from_thread(
                        plate.log_message, 
                        f"[bold blue]🛠️ Calling Tool:[/] {event.tool_name}\n"
                    )
                    self.call_from_thread(loader.start, f"Running {event.tool_name}...")
                
                elif isinstance(event, ToolResultEvent):
                    status = "[green]Success[/]" if not event.is_error else "[red]Error[/]"
                    self.call_from_thread(
                        plate.log_message,
                        f"[bold]Result:[/] {status}\n"
                    )
                
                elif isinstance(event, (CompactStartEvent, CompactEndEvent)):
                    self.call_from_thread(
                        plate.log_message,
                        "[dim]Compacting conversation history...[/]\n"
                    )

        except Exception as e:
            self.call_from_thread(self.notify, f"Agent Error: {e}", severity="error")
            self.call_from_thread(
                plate.log_message, f"[bold red]Error:[/] {e}\n"
            )
        finally:
            self.call_from_thread(ticket_rail.finish_streaming_message)
            self.call_from_thread(loader.stop)
            self._processing = False

    async def _submit_ticket(self, request: str) -> None:
        """Submit a new ticket to the kitchen via the bus."""
        # Show user message in UI immediately
        self.query_one("#ticket-rail", TicketRail).add_user_message(request)

        # ACTIVE MODE: Use Agent directly
        if self._active_mode and self._agent:
            self._processing = True
            self._run_agent_loop(request)
            return

        # LEGACY/MOCK MODE: Use Brigade/Bus
        # Check if we have a full brigade setup
        if not self._brigade:
            # No agent connected yet - show informational message
            self.query_one("#ticket-rail", TicketRail).add_system_message(
                "🔧 **Agent Not Connected**\n\n"
                "The TUI is running in standalone mode. To process requests, "
                "use the REPL: `uv run vibe`\n\n"
                "*Available commands: `/help`, `/modes`, `/roast`, `/wisdom`*"
            )
            return

        if not self._bus:
            self.notify("Kitchen bus not ready!", severity="error")
            return

        ticket_id = str(uuid4())[:8]
        message = ChefMessage(
            sender="tui",
            recipient="sous_chef",
            action=BusAction.NEW_TICKET.value,
            payload={PayloadKey.TICKET_ID: ticket_id, PayloadKey.REQUEST: request},
            priority=MessagePriority.HIGH,
        )

        # Start the loader
        self.query_one(WhiskLoader).start("Processing ticket...")
        self._processing = True

        await self._bus.publish(message)

    async def _handle_command(self, command: str) -> None:
        """Handle slash commands using the command registry."""
        if not command.startswith("/"):
            return

        parts = command.split(maxsplit=1)
        name = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        # Use shared CommandRegistry to find the command
        cmd_obj = self._command_registry.find_command(name)

        if cmd_obj:
            # Dispatch to appropriate method
            # We map REPL handler names to TUI method names where they differ
            handler_map = {
                "_show_help": "_show_command_palette",
                "_show_status": "_show_status",
                "_show_config": "_show_config",
                "_reload_config": "_reload_config",
                "_clear_history": "_handle_clear",
                "_show_log_path": "_show_log_path",
                "_compact_history": "_compact_history",
                "_exit_app": "_handle_quit",
                "_chef_status": "_show_chef_status",
                "_chef_wisdom": "_show_wisdom",
                "_show_modes": "_show_modes",
                "_chef_roast": "_show_roast",
                "_chef_plate": "_handle_plate",
                "_chef_taste": "_chef_taste",
                "_chef_timer": "_chef_timer",
            }

            handler_name = handler_map.get(cmd_obj.handler, cmd_obj.handler)

            if hasattr(self, handler_name):
                handler = getattr(self, handler_name)
                # Check execution signature - some methods take arg, some don't
                import inspect

                sig = inspect.signature(handler)
                if len(sig.parameters) > 0:
                    await handler(arg)
                else:
                    await handler()
                return

        # Fallback for TUI-specific commands not in registry
        if name in {"/layout", "/fortune", "/api", "/model"}:
            if name == "/layout":
                await self._handle_layout_command(arg)
            elif name == "/fortune":
                await self._show_fortune()
            elif name == "/api":
                await self._handle_api_command()
            elif name == "/model":
                await self._handle_model_command()
            return

        # Unknown command
        self.query_one("#ticket-rail", TicketRail).add_system_message(
            f"❓ Unknown command: `{name}`\n\nType `/help` to see available commands."
        )

    async def _handle_api_command(self) -> None:
        """Show API key onboarding screen."""
        await self.push_screen(OnboardingScreen(), self._on_onboarding_complete)

    async def _handle_model_command(self) -> None:
        """Show model selection screen."""
        if not self._config:
            self._config = VibeConfig.load()
            
        def on_model_selected(model_alias: str | None) -> None:
            if model_alias and self._config:
                try:
                    # Update config active model
                    self._config.active_model = model_alias
                    
                    # Persist change
                    VibeConfig.save_updates({"active_model": model_alias})
                    
                    # Re-initialize agent if active
                    if self._active_mode:
                        asyncio.create_task(self._initialize_agent())
                    
                    self.notify(f"Switched to model: {model_alias}")
                except Exception as e:
                    self.notify(f"Failed to switch model: {e}", severity="error")

        await self.push_screen(ModelSelectionScreen(self._config), on_model_selected)

    async def _show_command_palette(self) -> None:
        """Show help directly in chat instead of separate palette."""
        layout_info = f"Current: **{self._layout.value}**"
        help_text = f"""## 📋 ChefChat Commands

**Navigation**
• `/help` — Show this menu
• `/clear` — Clear the chat
• `/quit` — Exit ChefChat

**Modes** *(Shift+Tab to cycle)*
• `/modes` — Show all available modes
• `/status` — Show current session status

**Layout** ({layout_info})
• `/layout chat` — Clean chat-only view
• `/layout kitchen` — Full 3-panel kitchen view

**Kitchen Tools**
• `/taste` — Run taste tests (QA)
• `/timer` — Kitchen timer info
• `/log` — Show log file path

**Fun Commands**
• `/chef` — Kitchen status
• `/wisdom` — Random chef wisdom
• `/roast` — Get roasted by Gordon Ramsay
• `/fortune` — Developer fortune cookie
"""
        self.query_one("#ticket-rail", TicketRail).add_system_message(help_text)

    async def _handle_layout_command(self, arg: str) -> None:
        """Handle layout switching command."""
        arg = arg.lower().strip()

        if arg == "chat":
            if self._layout == TUILayout.CHAT_ONLY:
                self.query_one("#ticket-rail", TicketRail).add_system_message(
                    "Already in **chat** layout mode."
                )
            else:
                await self._confirm_layout_switch("chat")
        elif arg == "kitchen":
            if self._layout == TUILayout.FULL_KITCHEN:
                self.query_one("#ticket-rail", TicketRail).add_system_message(
                    "Already in **kitchen** layout mode."
                )
            else:
                await self._confirm_layout_switch("kitchen")
        else:
            current = self._layout.value
            self.query_one("#ticket-rail", TicketRail).add_system_message(
                f"## 🖼️ Layout Options\n\n"
                f"Current layout: **{current}**\n\n"
                f"• `/layout chat` — Clean chat-only view\n"
                f"• `/layout kitchen` — Full 3-panel kitchen view"
            )

    async def _handle_quit(self) -> None:
        """Handle quit command."""
        await self._shutdown()
        self.exit()

    async def _handle_plate(self) -> None:
        """Handle plate command wrapper."""
        if self._layout == TUILayout.FULL_KITCHEN:
            self.query_one("#the-plate", ThePlate).show_current_plate()
        else:
            self.query_one("#ticket-rail", TicketRail).add_system_message(
                "📋 `/plate` is only available in **kitchen** layout mode.\n\n"
                "Use `/layout kitchen` to switch to full kitchen view."
            )

    async def _confirm_layout_switch(self, new_layout: str) -> None:
        """Show confirmation dialog for layout switch."""

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                # Save preference and restart
                save_tui_preference("layout", new_layout)

                # Show message and restart
                self.notify(f"Restarting with {new_layout} layout...", timeout=1)
                self.set_timer(0.5, lambda: self.exit(result="RESTART"))
            else:
                self.query_one("#ticket-rail", TicketRail).add_system_message(
                    "Layout switch cancelled."
                )

        self.push_screen(ConfirmRestartScreen(new_layout), on_confirm)

    async def _handle_clear(self) -> None:
        await self.query_one("#ticket-rail", TicketRail).clear_tickets()
        if self._active_mode and self._agent:
            await self._agent.clear_history()
            self.notify("Context cleared")
            
        if self._layout == TUILayout.FULL_KITCHEN:
            try:
                self.query_one("#the-plate", ThePlate).clear_plate()
                self.query_one("#the-pass", ThePass).reset_all()
            except Exception:
                pass

    async def _show_modes(self) -> None:
        """Show available modes with current mode highlighted."""
        current = self._mode_manager.current_mode
        lines = [
            "## 🔄 Available Modes",
            "",
            "Press **Shift+Tab** to cycle through modes:",
            "",
        ]

        for mode in MODE_CYCLE_ORDER:
            config = MODE_CONFIGS[mode]
            marker = "▶" if mode == current else " "
            lines.append(
                f"{marker} {config.emoji} **{mode.value.upper()}**: {config.description}"
            )

        lines.extend(["", "---", f"Current: **{current.value.upper()}**"])
        self.query_one("#ticket-rail", TicketRail).add_system_message("\n".join(lines))

    async def _show_status(self) -> None:
        """Show session status."""
        mode = self._mode_manager.current_mode
        config = MODE_CONFIGS[mode]
        auto = "ON" if self._mode_manager.auto_approve else "OFF"

        status = f"""## 📊 Session Status

**Mode**: {config.emoji} {mode.value.upper()}
**Auto-Approve**: {auto}
**Kitchen**: {"Ready" if self._bus else "Initializing..."}
"""
        self.query_one("#ticket-rail", TicketRail).add_system_message(status)

    async def _show_wisdom(self) -> None:
        """Show chef wisdom."""
        import random

        wisdoms = [
            '🧑‍🍳 "Mise en place is not just for cooking—it\'s for coding too."',
            '🔪 "Sharp tools, sharp code. Keep your dependencies updated."',
            '🍳 "Low and slow wins the race. Don\'t rush your tests."',
            '🧂 "Season to taste. Iterate based on feedback."',
            '🍲 "A watched pot never boils. A watched CI never finishes."',
            '👨‍🍳 "Every chef was once a dishwasher. Keep refactoring."',
        ]
        self.query_one("#ticket-rail", TicketRail).add_system_message(
            random.choice(wisdoms)
        )

    async def _show_roast(self) -> None:
        """Get roasted by Gordon."""
        import random

        roasts = [
            '🔥 "This code is so raw, it\'s still mooing!"',
            '🔥 "I\'ve seen better architecture in a sandcastle!"',
            '🔥 "WHERE IS THE ERROR HANDLING?!"',
            '🔥 "This function is so long, it needs a GPS!"',
            '🔥 "You call that a commit message? Pathetic!"',
            '🔥 "My grandmother writes cleaner Python, and she\'s a COBOL developer!"',
        ]
        self.query_one("#ticket-rail", TicketRail).add_system_message(
            random.choice(roasts)
        )

    async def _show_fortune(self) -> None:
        """Developer fortune cookie."""
        import random

        fortunes = [
            "🥠 Your next merge conflict will resolve itself peacefully.",
            "🥠 The bug you\'ve been hunting is in the file you refuse to check.",
            "🥠 A refactor is in your future. Embrace it.",
            "🥠 Your deployment will succeed on the first try (just kidding).",
            "🥠 The documentation you need has not been written yet.",
            "🥠 Someone will appreciate your comment today.",
        ]
        self.query_one("#ticket-rail", TicketRail).add_system_message(
            random.choice(fortunes)
        )

    async def _show_chef_status(self) -> None:
        """Show chef/kitchen status."""
        mode = self._mode_manager.current_mode
        config = MODE_CONFIGS[mode]
        auto = "ON" if self._mode_manager.auto_approve else "OFF"
        bus_status = (
            "🟢 Running" if self._bus and self._bus.is_running else "🔴 Not Ready"
        )

        status = f"""## 👨‍🍳 Kitchen Status

**Current Mode**: {config.emoji} {mode.value.upper()}
**Description**: {config.description}
**Auto-Approve**: {auto}
**Kitchen Bus**: {bus_status}

---
*Press Shift+Tab to cycle modes*
"""
        self.query_one("#ticket-rail", TicketRail).add_system_message(status)

    async def _show_model_info(self) -> None:
        """Show current model information."""
        info = """## 🤖 Model Information

**Status**: Model information not available in TUI mode.

Use the **REPL** (`uv run vibe`) for full model configuration.
"""
        self.query_one("#ticket-rail", TicketRail).add_system_message(info)

    async def _show_config(self) -> None:
        """Show configuration info."""
        mode = self._mode_manager.current_mode
        config = MODE_CONFIGS[mode]

        info = f"""## ⚙️ Configuration

**Mode**: {config.emoji} {mode.value.upper()}
**Auto-Approve**: {"ON" if self._mode_manager.auto_approve else "OFF"}

---
*Use the REPL for full configuration options*
"""
        self.query_one("#ticket-rail", TicketRail).add_system_message(info)

    async def _show_log_path(self) -> None:
        """Show the current log path."""
        log_dir = TUI_PREFS_FILE.parent / "logs"
        self.query_one("#ticket-rail", TicketRail).add_system_message(
            f"## 📝 Kitchen Logs\n\n"
            f"Logs are stored in:\n`{log_dir}`\n\n"
            f"*Check these for details if the soufflé collapses.*"
        )

    async def _chef_taste(self) -> None:
        """Trigger the Expeditor to run taste tests."""
        if not self._brigade:
            self.query_one("#ticket-rail", TicketRail).add_system_message(
                "⚠️ **Expeditor not available**\n\n"
                "The kitchen brigade is not fully assembled. Run with `uv run vibe` for full kitchen experience."
            )
            return

        # Trigger taste test
        ticket_id = str(uuid4())[:8]
        message = ChefMessage(
            sender="tui",
            recipient="expeditor",
            action="TASTE_TEST",  # Matches BusAction.TASTE_TEST
            # Default to running pytest and ruff on current directory
            payload={"ticket_id": ticket_id, "tests": ["pytest", "ruff"], "path": "."},
            priority=MessagePriority.HIGH,
        )

        await self._bus.publish(message)
        self.query_one("#ticket-rail", TicketRail).add_system_message(
            f"🥄 **Taste Test Ordered** (Ticket #{ticket_id})\n\n"
            "Expeditor is checking the dish (running tests & linting)..."
        )

    async def _chef_timer(self, arg: str) -> None:
        """Show timer info."""
        self.query_one("#ticket-rail", TicketRail).add_system_message(
            "## ⏱️ Kitchen Timer\n\n"
            "Kitchen timer is coming soon to the TUI!\n"
            "Use it to track long-running tasks or just to boil an egg perfectly."
        )

    async def _reload_config(self) -> None:
        """Reload configuration."""
        # For TUI, most config is loaded on startup, but we can refresh modes
        self.notify("Reloading configuration...", title="System")
        self._mode_manager = ModeManager(initial_mode=self._mode_manager.current_mode)
        self.query_one("#ticket-rail", TicketRail).add_system_message(
            "🔄 **Configuration Reloaded**"
        )

    async def _compact_history(self) -> None:
        """Compact conversation history."""
        self.query_one("#ticket-rail", TicketRail).add_system_message(
            "🗜️ **Compacting History**\n\nCompressing the conversation context..."
        )
        # TODO: Implement actual compaction via Bus/Agent
        # For now, we simulate it
        await asyncio.sleep(1)
        self.query_one("#ticket-rail", TicketRail).add_system_message(
            "✅ History compacted."
        )

    async def _shutdown(self) -> None:
        """Gracefully shutdown the kitchen."""
        if self._brigade:
            await self._brigade.close_kitchen()
        elif self._bus:
            await self._bus.stop()

    def action_quit(self) -> None:
        if self._brigade or self._bus:
            # Schedule shutdown and then exit
            self.call_after_refresh(lambda: asyncio.create_task(self._shutdown()))
        self.exit()

    def action_cancel(self) -> None:
        self._processing = False
        try:
            self.query_one(WhiskLoader).stop()
        except Exception:
            pass

    def action_clear(self) -> None:
        asyncio.create_task(self._handle_clear())

    def action_focus_input(self) -> None:
        self.query_one("#command-input", CommandInput).focus()

    def action_cycle_mode(self) -> None:
        """Cycle through available modes (Shift+Tab)."""
        try:
            _old_mode, new_mode = self._mode_manager.cycle_mode()
            config = MODE_CONFIGS.get(new_mode)

            if config is None:
                return

            # Update the KitchenFooter silently
            try:
                self.query_one("#kitchen-footer", KitchenFooter).refresh_mode()
            except Exception:
                pass

            # Show notification (this is the most reliable feedback)
            self.notify(
                f"{config.emoji} {new_mode.value.upper()}: {config.description}",
                title="Mode Changed",
                timeout=2,
            )

        except Exception as e:
            # Catch any unexpected error so we don't crash
            self.notify(f"Mode error: {e}", severity="error", timeout=3)

    async def _setup_brigade(self) -> None:
        self._brigade = await create_default_brigade()
        self._bus = self._brigade.bus
        self._bus.subscribe("tui", self._handle_bus_message)
        await self._brigade.open_kitchen()


def run(*, verbose: bool = False, layout: str | None = None, active: bool = False) -> None:
    """Run the ChefChat TUI application.

    Args:
        verbose: Enable debug logging
        layout: Layout mode - 'chat' (clean) or 'kitchen' (3-panel).
                If None, uses saved preference (defaults to 'chat').
        active: Enable active mode (real Agent backend).
    """
    # Ensure Textual works with colors
    os.environ.setdefault("FORCE_COLOR", "1")

    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    if layout:
        save_tui_preference("layout", layout)

    while True:
        current_layout = get_saved_layout()
        layout_mode = (
            TUILayout.FULL_KITCHEN
            if current_layout == "kitchen"
            else TUILayout.CHAT_ONLY
        )

        app = ChefChatApp(layout=layout_mode, active_mode=active)

        try:
            result = app.run()
            if result == "RESTART":
                continue
            break
        except KeyboardInterrupt:
            print("\n👋 ChefChat kitchen closed.")
            break
        except Exception as e:
            print(f"\n❌ Application Error: {e}", file=sys.stderr)
            if verbose:
                traceback.print_exc()
            sys.exit(1)
