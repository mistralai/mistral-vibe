"""ChefChat Classic REPL – The Grand Service (Redesigned)
==========================================================

A premium, terminal-native REPL with full ChefChat integration.
Gordon Ramsay energy meets Michelin-star elegance.

Features:
    - Mode cycling with Shift+Tab
    - Easter egg commands (/chef, /wisdom, /roast)
    - Interactive tool approval (Waiter logic)
    - Beautiful Rich-powered dark UI
    - Professional kitchen atmosphere
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
from vibe.core.autocompletion.completers import CommandCompleter, PathCompleter
from vibe.cli.autocompletion.adapter import PromptToolkitCompleterAdapter

# =============================================================================
# CHEFCHAT INTEGRATIONS
# =============================================================================
from vibe.cli.mode_manager import MODE_TIPS, ModeManager, VibeMode

# Plating Integration (visual formatting)
from vibe.cli.plating import generate_plating

# UI Components (the new premium dark system)
from vibe.cli.ui_components import (
    COLORS,
    ApprovalDialog,
    ModeTransitionDisplay,
    ResponseDisplay,
    StatusBar,
    create_header,
    HelpDisplay,
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
    """Premium REPL interface for ChefChat – The Full Kitchen Experience."""

    def __init__(
        self, config: VibeConfig, initial_mode: VibeMode = VibeMode.NORMAL
    ) -> None:
        """Initialize the REPL with premium dark UI."""
        self.config = config
        self.mode_manager = ModeManager(initial_mode=initial_mode)
        self.console = Console()
        self.agent: Agent | None = None
        self._last_mode = initial_mode

        # Setup keybindings
        self.kb = KeyBindings()
        self._setup_keybindings()

        # Prompt styling with ChefChat dark colors
        self.style = Style.from_dict({
            "mode": f"bg:{COLORS['fire']} {COLORS['charcoal']} bold",
            "arrow": COLORS["fire"],
            "prompt": COLORS["silver"],
        })

        self.session: PromptSession[str] = PromptSession(
            key_bindings=self.kb, style=self.style
        )

        # Session tracking
        self.session_start_time = time.time()
        self.tools_executed = 0
        self._last_interrupt_time = 0.0

        # Autocompletion setup
        commands = [
            ("/help", "Show help menu"),
            ("/model", "Switch AI model"),
            ("/chef", "Kitchen status"),
            ("/wisdom", "Chef wisdom"),
            ("/roast", "Get roasted"),
            ("/fortune", "Dev fortune"),
            ("/plate", "Show plating"),
            ("/mode", "Current mode info"),
            ("/modes", "List modes"),
            ("/compact", "Compact conversation history"),
            ("/clear", "Clear history"),
            ("/status", "Show status"),
            ("/stats", "Show statistics"),
            ("/exit", "Exit application"),
            ("/quit", "Exit application"),
        ]
        self.completer = PromptToolkitCompleterAdapter([
            CommandCompleter(commands),
            PathCompleter(target_matches=20),
        ])

        # Re-create session with completer
        self.session: PromptSession[str] = PromptSession(
            key_bindings=self.kb,
            style=self.style,
            completer=self.completer,
            complete_while_typing=True,
        )

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

            # Force the prompt (and toolbar) to refresh with new mode
            event.app.invalidate()

    # =========================================================================
    # STARTUP BANNER - "Welcome to the Kitchen"
    # =========================================================================

    def _show_startup_banner(self) -> None:
        """Display the ChefChat startup banner with warmth."""
        from vibe.core import __version__
        from vibe.cli.ui_components import get_greeting

        greeting, greeting_emoji = get_greeting()

        banner_text = f"""
[bold {COLORS['fire']}]👨‍🍳 ChefChat[/bold {COLORS['fire']}] [dim]v{__version__}[/dim]

[{COLORS['cream']}]{greeting_emoji} {greeting}![/{COLORS['cream']}]
[{COLORS['silver']}]Ready to cook up something amazing?[/{COLORS['silver']}]
"""
        panel = Panel(
            Align.center(banner_text.strip()),
            box=box.ROUNDED,
            border_style=COLORS['fire'],
            subtitle=f"[{COLORS['smoke']}]Type /help for the menu[/{COLORS['smoke']}]",
            subtitle_align="center",
            padding=(1, 2),
        )
        self.console.print()
        self.console.print(panel)



    async def _handle_model_command(self) -> None:
        """Handle interactive model switching."""
        from rich.console import Group
        from rich.prompt import Prompt
        from rich.table import Table
        from vibe.core.config import ModelConfig

        # Create a display table for models
        table = Table(
            title=f"[{COLORS['primary']}]Available Models[/{COLORS['primary']}]",
            box=box.ROUNDED,
            border_style=COLORS['ash'],
            show_header=True,
            header_style=f"bold {COLORS['silver']}"
        )
        table.add_column("#", justify="right", style=COLORS['muted'])
        table.add_column("Alias", style=f"bold {COLORS['primary']}")
        table.add_column("Provider", style=COLORS['text'])
        table.add_column("Model ID", style=COLORS['muted'])

        models = self.config.models
        for idx, model in enumerate(models, 1):
            is_active = model.alias == self.config.active_model
            marker = "★" if is_active else str(idx)
            style = f"bold {COLORS['success']}" if is_active else COLORS['text']

            table.add_row(
                marker,
                model.alias,
                model.provider,
                model.name,
                style=style
            )

        self.console.print()
        self.console.print(table)
        self.console.print()

        # Ask for selection
        try:
            choice = Prompt.ask(
                f"[{COLORS['fire']}]Select model #[/{COLORS['fire']}]",
                default=str(next((i for i, m in enumerate(models, 1) if m.alias == self.config.active_model), 1))
            )

            if choice.strip():
                try:
                    idx = int(choice)
                    if 1 <= idx <= len(models):
                        selected_model = models[idx - 1]
                        self.config.active_model = selected_model.alias

                        # Re-initialize agent
                        await self._initialize_agent()

                        self.console.print(
                            f"\n  [{COLORS['sage']}]✓ Switched to {selected_model.alias} ({selected_model.provider})[/{COLORS['sage']}]\n"
                        )
                    else:
                        self.console.print(f"  [{COLORS['ember']}]Invalid number[/{COLORS['ember']}]")
                except ValueError:
                    # Check if they typed the alias directly
                    found = False
                    for model in models:
                        if model.alias.lower() == choice.lower():
                            self.config.active_model = model.alias
                            await self._initialize_agent()
                            self.console.print(
                                f"\n  [{COLORS['sage']}]✓ Switched to {model.alias}[/{COLORS['sage']}]\n"
                            )
                            found = True
                            break
                    if not found:
                        self.console.print(f"  [{COLORS['ember']}]Invalid selection[/{COLORS['ember']}]")

        except KeyboardInterrupt:
            self.console.print(f"\n  [{COLORS['honey']}]Cancelled[/{COLORS['honey']}]")

    def _show_stats(self) -> None:
        """Display session statistics - Today's Service."""
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

        table = Table(title="📊 Today's Service", box=box.ROUNDED, border_style=COLORS['ash'])
        table.add_column("Metric", style=f"bold {COLORS['silver']}", width=20)
        table.add_column("Value", style=COLORS['cream'])

        table.add_row("⏱️  Service Time", uptime_str)
        table.add_row("🔤 Tokens Used", f"{token_count:,}")
        table.add_row("🔧 Tools Executed", str(self.tools_executed))
        table.add_row("🎯 Current Mode", self.mode_manager.current_mode.value.upper())
        table.add_row("⚡ Auto-Approve", "ON" if self.mode_manager.auto_approve else "OFF")

        self.console.print()
        self.console.print(table)
        self.console.print()

    # =========================================================================
    # UI HELPERS
    # =========================================================================

    def _get_bottom_toolbar(self) -> Any:
        """Render the bottom status toolbar."""
        from html import escape
        from prompt_toolkit.formatted_text import HTML

        # Status
        mode_name = escape(self.mode_manager.current_mode.value.upper())
        mode_desc = escape(self.mode_manager.config.description)
        approval_status = "ON" if self.mode_manager.auto_approve else "OFF"
        approval_color = COLORS['sage'] if self.mode_manager.auto_approve else COLORS['honey']

        return HTML(
            f' <b><style fg="{COLORS["fire"]}">[Shift+Tab]</style></b> Switch Mode '
            f'<style fg="{COLORS["smoke"]}">•</style> '
            f'<b><style fg="{COLORS["silver"]}">{mode_name}</style></b> '
            f'<style fg="{COLORS["smoke"]}">•</style> '
            f'<style fg="{COLORS["smoke"]}">{mode_desc}</style> '
            f'<style fg="{COLORS["smoke"]}">•</style> '
            f'<b>Auto:</b> <style fg="{approval_color}">{approval_status}</style> '
            f'<style fg="{COLORS["smoke"]}">•</style> '
            f'<b><style fg="{COLORS["fire"]}">[Ctrl+C]</style></b> Stop'
        )

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
    # WAITER LOGIC - Tool Approval
    # =========================================================================

    async def ask_user_approval(
        self, tool_name: str, args: dict[str, Any], tool_call_id: str
    ) -> tuple[str, str | None]:
        """Display Order Confirmation dialog – the Waiter logic."""
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
                f"[{COLORS['fire']}]▶[/{COLORS['fire']}] [bold]Your call, Chef[/bold]",
                choices=["y", "n", "always", "Y", "N", "a"],
                default="y",
                show_choices=False,
            )
        except (KeyboardInterrupt, EOFError):
            return (ApprovalResponse.NO, "Interrupted")

        match choice.lower().strip():
            case "y" | "yes" | "":
                self.console.print(
                    f"  [{COLORS['sage']}]✓ Oui, Chef![/{COLORS['sage']}]"
                )
                return (ApprovalResponse.YES, None)
            case "always" | "a":
                self.console.print(
                    f"  [{COLORS['honey']}]⚡ Trusting you for this session[/{COLORS['honey']}]"
                )
                return (ApprovalResponse.ALWAYS, None)
            case _:
                self.console.print(f"  [{COLORS['ember']}]✗ Not today[/{COLORS['ember']}]")
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
                f"[{COLORS['ember']}]Agent not initialized[/{COLORS['ember']}]"
            )
            return

        self._handle_mode_change()
        response_text = ""

        with Live(
            Spinner(
                "dots", text=f"[{COLORS['fire']}] Cooking...[/{COLORS['fire']}]"
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
                    f"\n  [{COLORS['honey']}]⚠ Stopped by Chef[/{COLORS['honey']}]"
                )
                return
            except Exception as e:
                self.console.print(
                    f"\n  [{COLORS['ember']}]Error: {e}[/{COLORS['ember']}]"
                )
                return

        # Display response with elegant styling
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
        self.console.print()

        while True:
            try:
                # Dynamic prompt that updates with mode changes
                def get_prompt() -> list[tuple[str, str]]:
                    emoji = self.mode_manager.config.emoji
                    mode_name = self.mode_manager.current_mode.value.upper()
                    return [
                        ("class:mode", f" {emoji} {mode_name} "),
                        ("class:arrow", " "),
                        ("class:prompt", "› "),
                    ]

                with patch_stdout():
                    user_input = await self.session.prompt_async(
                        get_prompt,
                        bottom_toolbar=self._get_bottom_toolbar,
                        refresh_interval=0.5,
                    )

                # Handle exit
                if user_input.strip().lower() in {"exit", "quit", "/exit", "/quit"}:
                    self.console.print(
                        f"\n[{COLORS['silver']}]👋 Service complete. À bientôt, Chef![/{COLORS['silver']}]\n"
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
                current_time = time.time()
                if current_time - self._last_interrupt_time < 1.0:
                    self.console.print(
                        f"\n[{COLORS['silver']}]👋 Kitchen closed. Service finished.[/{COLORS['silver']}]\n"
                    )
                    sys.exit(0)
                else:
                    self._last_interrupt_time = current_time
                    self.console.print(
                        f"\n  [{COLORS['honey']}]⚠ Press Ctrl+C again to exit[/{COLORS['honey']}]"
                    )
                continue
            except EOFError:
                self.console.print(
                    f"\n[{COLORS['silver']}]👋 Service complete. À bientôt, Chef![/{COLORS['silver']}]\n"
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
        # EASTER EGG COMMANDS - The Secret Menu
        # =====================================================================

        if cmd == "/chef":
            # Kitchen status with mode info - returns Panel now
            self.console.print()
            self.console.print(get_kitchen_status(self.mode_manager))
            self.console.print()

        elif cmd == "/wisdom":
            # Random chef wisdom - returns Panel now
            self.console.print()
            self.console.print(get_random_wisdom())
            self.console.print()

        elif cmd == "/roast":
            # Gordon Ramsay style roast - returns Panel now
            self.console.print()
            self.console.print(get_random_roast())
            self.console.print()

        elif cmd == "/fortune":
            # Developer fortune cookie - returns Panel now
            self.console.print()
            self.console.print(get_dev_fortune())
            self.console.print()

        # =====================================================================
        # PLATING COMMAND
        # =====================================================================

        elif cmd == "/plate":
            # Show the current session "plating" - returns Panel now
            self.console.print()
            stats = self.agent.stats if self.agent else None
            self.console.print(generate_plating(self.mode_manager, stats))
            self.console.print()

        # =====================================================================
        # STANDARD COMMANDS
        # =====================================================================

        elif cmd in {"/help", "/h", "/?"}:
            self.console.print()
            self.console.print(HelpDisplay.render())
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

        elif cmd == "/model":
            await self._handle_model_command()

        elif cmd == "/modes":
            # Use the easter_eggs display - returns Panel now
            self.console.print()
            self.console.print(get_modes_display(self.mode_manager))
            self.console.print()

        elif cmd == "/clear":
            if self.agent:
                self.agent.clear_history()
                self.console.print(
                    f"  [{COLORS['sage']}]✓ Conversation cleared - Fresh start![/{COLORS['sage']}]\n"
                )
            else:
                self.console.print(
                    f"  [{COLORS['honey']}]No active session to clear[/{COLORS['honey']}]\n"
                )

        elif cmd in {"/compact", "/summarize"}:
            if self.agent:
                with Live(
                    Spinner(
                        "dots",
                        text=f"[{COLORS['fire']}] Compacting history...[/{COLORS['fire']}]",
                    ),
                    console=self.console,
                    refresh_per_second=10,
                    transient=True,
                ):
                    try:
                        summary = await self.agent.compact()
                        self.console.print(
                            f"  [{COLORS['sage']}]✓ Conversation compacted![/{COLORS['sage']}]"
                        )
                        # Show a preview of the summary
                        preview = (
                            summary[:200] + "..." if len(summary) > 200 else summary
                        )
                        self.console.print(
                            f"  [{COLORS['silver']}]Summary preview: {preview}[/{COLORS['silver']}]\n"
                        )
                    except Exception as e:
                        self.console.print(
                            f"  [{COLORS['ember']}]Failed to compact history: {e}[/{COLORS['ember']}]\n"
                        )
            else:
                self.console.print(
                    f"  [{COLORS['honey']}]No active session to compact[/{COLORS['honey']}]\n"
                )

        elif cmd == "/status":
            self.console.print()
            self.console.print(StatusBar.render(self.mode_manager.auto_approve))
            self.console.print()

        elif cmd == "/stats":
            # Session statistics - Today's Service
            self._show_stats()

        else:
            self.console.print(
                f"  [{COLORS['honey']}]Unknown command: {command}[/{COLORS['honey']}]"
            )
            self.console.print(
                f"  [{COLORS['smoke']}]Type /help for the menu[/{COLORS['smoke']}]\n"
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
