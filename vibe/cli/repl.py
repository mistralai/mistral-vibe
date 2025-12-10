"""ChefChat Classic REPL — The Grand Service
=============================================

A premium, terminal-native REPL with full ChefChat integration.
Michelin-star elegance meets developer productivity.

Features:
    - Mode cycling with Shift+Tab
    - Easter egg commands (/chef, /wisdom, /roast)
    - Interactive tool approval (Waitor logic)
    - Beautiful Rich-powered UI
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import TYPE_CHECKING, Any, NoReturn

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich import box
from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax

# Easter Eggs Integration
from vibe.cli.easter_eggs import (
    get_dev_fortune,
    get_kitchen_status,
    get_modes_display,
    get_random_roast,
    get_random_wisdom,
)

# =============================================================================
# CHEFCHAT INTEGRATIONS - The "Loose Wires" Now Connected
# =============================================================================
from vibe.cli.mode_manager import MODE_TIPS, ModeManager, VibeMode

# Plating Integration (visual formatting)
from vibe.cli.plating import generate_plating

# UI Components (the new elegant system)
from vibe.cli.ui_components import (
    COLORS,
    ApprovalDialog,
    ModeTransitionDisplay,
    ResponseDisplay,
    StatusBar,
    create_header,
)

# Core imports
from vibe.core.agent import Agent
from vibe.core.config import VibeConfig
from vibe.core.error_handler import ChefErrorHandler
from vibe.core.types import AssistantEvent, ToolCallEvent, ToolResultEvent
from vibe.core.utils import (
    ApprovalResponse,
    CancellationReason,
    get_user_cancellation_message,
)

if TYPE_CHECKING:
    pass


# =============================================================================
# REPL CLASS
# =============================================================================


class ChefChatREPL:
    """Premium REPL interface for ChefChat — The Full Kitchen Experience."""

    def __init__(
        self, config: VibeConfig, initial_mode: VibeMode = VibeMode.NORMAL
    ) -> None:
        """Initialize the REPL."""
        self.config = config
        self.mode_manager = ModeManager(initial_mode=initial_mode)
        self.console = Console()
        self.agent: Agent | None = None
        self._last_mode = initial_mode

        # Setup keybindings
        self.kb = KeyBindings()
        self._setup_keybindings()

        # Prompt styling with ChefChat colors
        self.style = Style.from_dict({
            "mode": f"bg:{COLORS['primary']} #ffffff bold",
            "arrow": COLORS["primary"],
            "prompt": COLORS["muted"],
        })

        self.session: PromptSession[str] = PromptSession(
            key_bindings=self.kb, style=self.style
        )

        # Session tracking for /stats
        self.session_start_time = time.time()
        self.tools_executed = 0

    # =========================================================================
    # KEYBINDINGS
    # =========================================================================

    def _setup_keybindings(self) -> None:
        """Setup keyboard bindings with elegant mode transitions."""
        from prompt_toolkit.keys import Keys

        @self.kb.add(Keys.BackTab)
        def cycle_mode_handler(event: Any) -> None:
            old_mode, new_mode = self.mode_manager.cycle_mode()
            self._last_mode = new_mode

            # Update agent's approval state
            if self.agent:
                self.agent.auto_approve = self.mode_manager.auto_approve
                if self.mode_manager.auto_approve:
                    self.agent.approval_callback = None
                else:
                    self.agent.approval_callback = self.ask_user_approval

            # Get tips for the new mode
            tips = MODE_TIPS.get(new_mode, [])

            # Get mode info
            new_emoji = self.mode_manager.config.emoji
            description = self.mode_manager.config.description

            # ═══════════════════════════════════════════════════════════════
            # FIX D.4: Use Rich console.print() instead of output.write()
            # output.write() is append-only, causing mode transitions to stack
            # console.print() renders cleanly without stacking issues
            # ═══════════════════════════════════════════════════════════════
            panel = ModeTransitionDisplay.render(
                old_mode=old_mode.value.upper(),
                new_mode=new_mode.value.upper(),
                new_emoji=new_emoji,
                description=description,
                tips=tips,
            )
            self.console.print()
            self.console.print(panel)

            # Force the prompt to refresh with new mode
            event.app.invalidate()

    # =========================================================================
    # STARTUP BANNER
    # =========================================================================

    def _show_startup_banner(self) -> None:
        """Display the ChefChat startup banner."""
        from vibe.core import __version__

        banner_text = f"""
[bold #FF7000]👨‍🍳 ChefChat[/bold #FF7000] [dim]v{__version__}[/dim]
[dim]The Tastiest AI Agent CLI[/dim]
"""
        panel = Panel(
            Align.center(banner_text.strip()),
            box=box.DOUBLE,
            border_style="#FF7000",
            subtitle="[dim]Type /chef for help · Shift+Tab to switch modes[/dim]",
            subtitle_align="center",
            padding=(1, 2),
        )
        self.console.print()
        self.console.print(panel)

    def _show_stats(self) -> None:
        """Display session statistics - Menu du Jour."""
        from rich.table import Table

        uptime_seconds = int(time.time() - self.session_start_time)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours > 0:
            uptime_str = f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            uptime_str = f"{minutes}m {seconds}s"
        else:
            uptime_str = f"{seconds}s"

        # Get token count from agent if available
        token_count = 0
        if self.agent and hasattr(self.agent, "stats"):
            token_count = getattr(self.agent.stats, "total_tokens", 0)

        table = Table(title="📊 Menu du Jour", box=box.SIMPLE)
        table.add_column("Metric", style=f"bold {COLORS['primary']}")
        table.add_column("Value", style=COLORS["success"])

        table.add_row("⏱️  Uptime", uptime_str)
        table.add_row("🔤 Tokens Used", f"{token_count:,}")
        table.add_row("🔧 Tools Executed", str(self.tools_executed))
        table.add_row("🎯 Current Mode", self.mode_manager.current_mode.value.upper())

        self.console.print()
        self.console.print(table)
        self.console.print()

    # =========================================================================
    # AGENT INITIALIZATION
    # =========================================================================

    async def _initialize_agent(self) -> None:
        """Initialize the Agent with approval callback."""
        self.agent = Agent(
            self.config,
            auto_approve=self.mode_manager.auto_approve,
            enable_streaming=True,
            mode_manager=self.mode_manager,
        )

        if not self.mode_manager.auto_approve:
            self.agent.approval_callback = self.ask_user_approval

    # =========================================================================
    # WAITOR LOGIC - Tool Approval
    # =========================================================================

    async def ask_user_approval(
        self, tool_name: str, args: dict[str, Any], tool_call_id: str
    ) -> tuple[str, str | None]:
        """Display Order Confirmation dialog — the Waitor logic."""
        # Format arguments
        args_json = json.dumps(args, indent=2, default=str)
        if len(args_json) > 400:
            args_json = args_json[:400] + "\n  ... (truncated)"

        syntax = Syntax(args_json, "json", theme="monokai", line_numbers=False)

        # Display elegant approval dialog
        self.console.print()
        self.console.print(ApprovalDialog.render(tool_name, syntax))

        # Get user decision
        from rich.prompt import Prompt

        try:
            choice = Prompt.ask(
                f"[{COLORS['primary']}]▶[/{COLORS['primary']}] [bold]Allow?[/bold]",
                choices=["y", "n", "always", "Y", "N"],
                default="y",
                show_choices=False,
            )
        except (KeyboardInterrupt, EOFError):
            return (ApprovalResponse.NO, "Interrupted")

        match choice.lower().strip():
            case "y" | "yes" | "":
                self.console.print(
                    f"  [{COLORS['success']}]✓ Approved[/{COLORS['success']}]"
                )
                return (ApprovalResponse.YES, None)
            case "always" | "a":
                self.console.print(
                    f"  [{COLORS['warning']}]⚡ Auto-approved for session[/{COLORS['warning']}]"
                )
                return (ApprovalResponse.ALWAYS, None)
            case _:
                self.console.print(f"  [{COLORS['error']}]✗ Denied[/{COLORS['error']}]")
                return (
                    ApprovalResponse.NO,
                    str(
                        get_user_cancellation_message(
                            CancellationReason.OPERATION_CANCELLED
                        )
                    ),
                )

    def _handle_mode_change(self) -> None:
        """Update agent when mode changes."""
        if self.agent:
            self.agent.auto_approve = self.mode_manager.auto_approve
            if self.mode_manager.auto_approve:
                self.agent.approval_callback = None
            else:
                self.agent.approval_callback = self.ask_user_approval

    # =========================================================================
    # AGENT RESPONSE HANDLING
    # =========================================================================

    async def _handle_agent_response(self, user_input: str) -> None:
        """Process user input through the Agent."""
        if not self.agent:
            self.console.print(
                f"[{COLORS['error']}]Agent not initialized[/{COLORS['error']}]"
            )
            return

        self._handle_mode_change()
        response_text = ""

        with Live(
            Spinner(
                "dots", text=f"[{COLORS['primary']}] Cooking...[/{COLORS['primary']}]"
            ),
            console=self.console,
            refresh_per_second=10,
            transient=True,
        ):
            try:
                async for event in self.agent.act(user_input):
                    if isinstance(event, AssistantEvent):
                        response_text += event.content

                    elif isinstance(event, ToolCallEvent):
                        self.console.print(
                            ResponseDisplay.render_tool_call(event.tool_name)
                        )

                    elif isinstance(event, ToolResultEvent):
                        if event.error:
                            self.console.print(
                                ResponseDisplay.render_tool_result(
                                    False, str(event.error)[:50]
                                )
                            )
                        elif event.skipped:
                            self.console.print(
                                ResponseDisplay.render_tool_result(
                                    False, event.skip_reason or "Skipped"
                                )
                            )
                        else:
                            self.tools_executed += 1
                            self.console.print(ResponseDisplay.render_tool_result(True))

            except KeyboardInterrupt:
                self.console.print(
                    f"\n  [{COLORS['warning']}]⚠ Interrupted[/{COLORS['warning']}]"
                )
                return
            except Exception as e:
                self.console.print(
                    f"\n  [{COLORS['error']}]Error: {e}[/{COLORS['error']}]"
                )
                return

        # Display response with plating
        if response_text.strip():
            self.console.print()
            self.console.print(ResponseDisplay.render_response(Markdown(response_text)))
        self.console.print()

    # =========================================================================
    # MAIN LOOP
    # =========================================================================

    async def run_async(self) -> NoReturn:
        """Run the main REPL loop."""
        await self._initialize_agent()

        # Show startup banner
        self._show_startup_banner()

        # Print elegant header
        self.console.print()
        self.console.print(create_header(self.config, self.mode_manager))

        # Print status bar with shortcuts
        self.console.print(StatusBar.render(self.mode_manager.auto_approve))
        self.console.print()

        while True:
            try:
                # Use a callable prompt that re-evaluates on each render
                # This ensures the mode updates immediately after Shift+Tab
                def get_prompt() -> list[tuple[str, str]]:
                    emoji = self.mode_manager.config.emoji
                    mode_name = self.mode_manager.current_mode.value.upper()
                    return [
                        ("class:mode", f" {emoji} {mode_name} "),
                        ("class:arrow", " "),
                        ("class:prompt", "› "),
                    ]

                with patch_stdout():
                    user_input = await self.session.prompt_async(get_prompt)

                # Handle exit
                if user_input.strip().lower() in {"exit", "quit", "/exit", "/quit"}:
                    self.console.print(
                        f"\n[{COLORS['muted']}]👋 Goodbye from the Kitchen![/{COLORS['muted']}]\n"
                    )
                    sys.exit(0)

                if not user_input.strip():
                    continue

                # Handle commands (including easter eggs!)
                if user_input.strip().startswith("/"):
                    await self._handle_command(user_input.strip())
                    continue

                # Process with agent
                await self._handle_agent_response(user_input)

            except KeyboardInterrupt:
                self.console.print()
                continue
            except EOFError:
                self.console.print(
                    f"\n[{COLORS['muted']}]👋 Goodbye from the Kitchen![/{COLORS['muted']}]\n"
                )
                sys.exit(0)
            except Exception as e:
                ChefErrorHandler.display_error(e, context="REPL", show_traceback=False)

    # =========================================================================
    # COMMAND HANDLING - Including Easter Eggs!
    # =========================================================================

    async def _handle_command(self, command: str) -> None:
        """Handle slash commands including easter eggs."""
        cmd = command.lower().strip()

        # =====================================================================
        # EASTER EGG COMMANDS - From vibe/cli/easter_eggs.py
        # =====================================================================

        if cmd == "/chef":
            # Kitchen status with mode info - THE SIGNATURE COMMAND
            self.console.print()
            status = get_kitchen_status(self.mode_manager)
            self.console.print(
                Panel(
                    status,
                    title=f"[{COLORS['primary']}]👨‍🍳 Kitchen Status[/{COLORS['primary']}]",
                    border_style=COLORS["primary"],
                )
            )
            self.console.print()

        elif cmd == "/wisdom":
            # Random chef wisdom
            self.console.print()
            wisdom = get_random_wisdom()
            self.console.print(
                Panel(
                    wisdom,
                    title=f"[{COLORS['primary']}]🧠 Chef's Wisdom[/{COLORS['primary']}]",
                    border_style=COLORS["secondary"],
                )
            )
            self.console.print()

        elif cmd == "/roast":
            # Gordon Ramsay style roast
            self.console.print()
            roast = get_random_roast()
            self.console.print(
                Panel(
                    Markdown(roast),
                    title=f"[{COLORS['error']}]🔥 Chef Ramsay Says[/{COLORS['error']}]",
                    border_style=COLORS["error"],
                )
            )
            self.console.print()

        elif cmd == "/fortune":
            # Developer fortune cookie - Feature 3.2
            self.console.print()
            fortune = get_dev_fortune()
            self.console.print(
                Panel(
                    Markdown(fortune),
                    title=f"[{COLORS['primary']}]🥠 Fortune Cookie[/{COLORS['primary']}]",
                    border_style=COLORS["secondary"],
                )
            )
            self.console.print()

        # =====================================================================
        # PLATING COMMAND - From vibe/cli/plating.py
        # =====================================================================

        elif cmd == "/plate":
            # Show the current session "plating"
            self.console.print()
            stats = self.agent.stats if self.agent else None
            plating = generate_plating(self.mode_manager, stats)
            self.console.print(plating)
            self.console.print()

        # =====================================================================
        # STANDARD COMMANDS
        # =====================================================================

        elif cmd in {"/help", "/h", "/?"}:
            self.console.print()
            self.console.print(self._build_help_panel())
            self.console.print()

        elif cmd == "/mode":
            tips = MODE_TIPS.get(self.mode_manager.current_mode, [])
            self.console.print()
            self.console.print(
                ModeTransitionDisplay.render(
                    old_mode="",
                    new_mode=self.mode_manager.current_mode.value.upper(),
                    new_emoji=self.mode_manager.config.emoji,
                    description=self.mode_manager.config.description,
                    tips=tips,
                )
            )
            self.console.print()

        elif cmd == "/modes":
            # Use the easter_eggs display for consistency
            self.console.print()
            modes_display = get_modes_display(self.mode_manager)
            self.console.print(
                Panel(
                    modes_display,
                    title=f"[{COLORS['primary']}]Available Modes[/{COLORS['primary']}]",
                    border_style=COLORS["secondary"],
                )
            )
            self.console.print()

        elif cmd == "/clear":
            if self.agent:
                self.agent.clear_history()
                self.console.print(
                    f"  [{COLORS['success']}]✓ Conversation cleared[/{COLORS['success']}]\n"
                )
            else:
                self.console.print(
                    f"  [{COLORS['warning']}]No active session[/{COLORS['warning']}]\n"
                )

        elif cmd == "/status":
            self.console.print()
            self.console.print(StatusBar.render(self.mode_manager.auto_approve))
            self.console.print()

        elif cmd == "/stats":
            # Session statistics - Menu du Jour
            self._show_stats()

        else:
            self.console.print(
                f"  [{COLORS['warning']}]Unknown: {command}[/{COLORS['warning']}]"
            )
            self.console.print(
                f"  [{COLORS['muted']}]Type /help for commands[/{COLORS['muted']}]\n"
            )

    def _build_help_panel(self) -> Panel:
        """Build the help panel with all commands including easter eggs."""
        from rich.table import Table

        table = Table(show_header=False, box=None, padding=(0, 3))
        table.add_column("key", style=f"bold {COLORS['primary']}")
        table.add_column("desc", style=COLORS["text"])

        # Standard commands
        table.add_row("/help", "Show this help")
        table.add_row("/mode", "Show current mode details")
        table.add_row("/modes", "List all available modes")
        table.add_row("/clear", "Clear conversation history")
        table.add_row("/status", "Show session status")
        table.add_row("/exit", "Exit ChefChat")
        table.add_row("", "")

        # Easter egg commands - THE SECRET MENU
        table.add_row("", "[dim]── Secret Menu ──[/dim]")
        table.add_row("/chef", "🍳 Kitchen status report")
        table.add_row("/wisdom", "🧠 Random chef wisdom")
        table.add_row("/roast", "🔥 Get roasted by Chef Ramsay")
        table.add_row("/fortune", "🥠 Open a fortune cookie")
        table.add_row("/plate", "🍽️ Present your work beautifully")
        table.add_row("/stats", "📊 Session statistics")
        table.add_row("", "")

        # Keybindings
        table.add_row("", "[dim]── Keybindings ──[/dim]")
        table.add_row("Shift+Tab", "Cycle through modes")
        table.add_row("Ctrl+C", "Cancel current operation")

        return Panel(
            table,
            title=f"[{COLORS['primary']}]🍳 ChefChat Commands[/{COLORS['primary']}]",
            border_style=COLORS["secondary"],
            padding=(1, 2),
        )

    def run(self) -> NoReturn:
        """Run the REPL."""
        asyncio.run(self.run_async())


# =============================================================================
# ENTRY POINT
# =============================================================================


def run_repl(config: VibeConfig, initial_mode: VibeMode = VibeMode.NORMAL) -> None:
    """Entry point for the REPL."""
    repl = ChefChatREPL(config, initial_mode)
    repl.run()
