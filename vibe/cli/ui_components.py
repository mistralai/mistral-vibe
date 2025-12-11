"""ChefChat REPL UI Components
=============================

Elegant, Michelin-star UI components for the ChefChat REPL.
Design philosophy: "Mistral Vibe Aesthetics meets Michelin Star Elegance"

Color Palette:
    - Primary (accent): #FF7000 (Mistral Orange)
    - Secondary (borders): #404040 (Dark Grey)
    - Text: #E0E0E0 (Off-white)
    - Muted: #666666 (Dim)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from vibe.cli.mode_manager import ModeManager
    from vibe.core.config import VibeConfig


# =============================================================================
# COLOR CONSTANTS
# =============================================================================

COLORS = {
    # Technical names
    "primary": "#FF7000",  # Mistral Orange - accents, active elements
    "secondary": "#404040",  # Dark Grey - borders, inactive
    "text": "#E0E0E0",  # Off-white - readable text
    "muted": "#666666",  # Dim - secondary info
    "success": "#00D26A",  # Green - success states
    "warning": "#FFB800",  # Amber - warnings
    "error": "#FF4444",  # Red - errors
    "bg_dark": "#1A1A1A",  # Dark background
    "bg_subtle": "#252525",  # Slightly lighter bg

    # Kitchen-themed aliases (for repl.py compatibility)
    "fire": "#FF7000",  # Same as primary - the flame of the kitchen
    "charcoal": "#1A1A1A",  # Same as bg_dark - the grill
    "silver": "#E0E0E0",  # Same as text - polished steel
    "smoke": "#666666",  # Same as muted - subtle smoke
    "sage": "#00D26A",  # Same as success - fresh herbs
    "honey": "#FFB800",  # Same as warning - golden honey
    "ember": "#FF4444",  # Hot embers
    "cream": "#F5F5DC",  # Cream color for highlights
    "ash": "#404040",  # Cool ash
    "gold": "#FFD700",  # Gold for highlights
    "lavender": "#E6E6FA", # Lavender for subtle accents
}


# =============================================================================
# HEADER COMPONENT (The Pass)
# =============================================================================


@dataclass
class HeaderData:
    """Data for the header display."""

    model: str
    mode_indicator: str
    mode_emoji: str
    workdir: str
    version: str = ""
    context_used: int = 0
    context_max: int = 32000


class HeaderDisplay:
    """Elegant header component showing metadata in a clean grid layout.

    Visual output:
    ┌──────────────────────────────────────────────────────────────────┐
    │  👨‍🍳 ChefChat                                    ✋ NORMAL        │
    │  ──────────────────────────────────────────────────────────────  │
    │  mistral-large-latest   │   📂 ~/project   │   0/32k tokens     │
    └──────────────────────────────────────────────────────────────────┘
    """

    def __init__(self, data: HeaderData) -> None:
        self.data = data

    def render(self) -> Panel:
        """Render the header as a Rich Panel."""
        # Top row: Brand + Mode
        top_row = Table.grid(expand=True)
        top_row.add_column("brand", justify="left", ratio=1)
        top_row.add_column("mode", justify="right", ratio=1)

        brand = Text()
        brand.append("👨‍🍳 ", style="bold")
        brand.append("ChefChat", style=f"bold {COLORS['primary']}")
        if self.data.version:
            brand.append(f" v{self.data.version}", style=COLORS["muted"])

        mode = Text()
        mode.append(f"{self.data.mode_emoji} ", style="bold")
        mode.append(self.data.mode_indicator, style=f"bold {COLORS['primary']}")

        top_row.add_row(brand, mode)

        # Separator
        separator = Text("─" * 60, style=COLORS["secondary"])

        # Bottom row: Meta info
        meta_row = Table.grid(expand=True)
        meta_row.add_column("model", justify="left", ratio=2)
        meta_row.add_column("path", justify="center", ratio=2)
        meta_row.add_column("context", justify="right", ratio=1)

        model_text = Text(self.data.model, style=COLORS["text"])

        # Truncate path if needed
        workdir = self.data.workdir
        if len(workdir) > 25:
            workdir = "~" + workdir[-(24):]
        path_text = Text()
        path_text.append("📂 ", style="dim")
        path_text.append(workdir, style=COLORS["muted"])

        # Context display
        ctx_text = Text()
        ctx_used_k = self.data.context_used / 1000
        ctx_max_k = self.data.context_max / 1000
        ctx_text.append(f"{ctx_used_k:.0f}/{ctx_max_k:.0f}k", style=COLORS["muted"])

        meta_row.add_row(model_text, path_text, ctx_text)

        # Combine all elements
        content = Group(top_row, Text(), separator, Text(), meta_row)

        return Panel(content, border_style=COLORS["secondary"], padding=(0, 2))


# =============================================================================
# PROMPT BUILDER (The Station)
# =============================================================================


class PromptBuilder:
    """Build elegant prompt strings for prompt_toolkit.

    Creates a Powerline-style prompt segment:
        [bg=orange] 👨‍🍳 NORMAL [/] ›
    """

    # Safe Unicode characters for Powerline-style (no special fonts needed)
    SEGMENT_END = ""  # Simple space separator
    ARROW = "›"

    @staticmethod
    def build_prompt(mode_emoji: str, mode_name: str) -> str:
        """Build HTML-formatted prompt for prompt_toolkit.

        Args:
            mode_emoji: Emoji for the mode (✋, ⚡, etc.)
            mode_name: Mode name (NORMAL, AUTO, etc.)

        Returns:
            HTML string for prompt_toolkit
        """
        # Format: [emoji MODE] ›
        return (
            f'<style bg="#FF7000" fg="white"> {mode_emoji} {mode_name} </style>'
            f'<style fg="#FF7000"> </style>'
            f'<style fg="#666666">›</style> '
        )

    @staticmethod
    def build_prompt_simple(mode_emoji: str, mode_name: str) -> str:
        """Build simpler prompt without background colors.

        Fallback for terminals with limited support.
        """
        return f"<mode>{mode_emoji} {mode_name}</mode> <prompt>›</prompt> "


# =============================================================================
# STATUS BAR (The Footer)
# =============================================================================


class StatusBar:
    """Sticky status bar with keyboard shortcuts.

    Visual output:
    ───────────────────────────────────────────────────────────────────────
    [Shift+Tab] Mode  •  [/] Commands  •  [Ctrl+C] Cancel  •  auto-approve: off
    """

    @staticmethod
    def render(auto_approve: bool = False) -> Text:
        """Render the status bar."""
        bar = Text()

        # Separator line
        bar.append("─" * 70, style=COLORS["secondary"])
        bar.append("\n")

        # Shortcuts
        shortcuts = [("Shift+Tab", "Mode"), ("/help", "Commands"), ("Ctrl+C", "Cancel")]

        for i, (key, action) in enumerate(shortcuts):
            if i > 0:
                bar.append("  •  ", style=COLORS["muted"])
            bar.append(f"[{key}]", style=f"bold {COLORS['primary']}")
            bar.append(f" {action}", style=COLORS["muted"])

        # Auto-approve status
        bar.append("  •  ", style=COLORS["muted"])
        bar.append("auto-approve: ", style=COLORS["muted"])
        if auto_approve:
            bar.append("on", style=f"bold {COLORS['success']}")
        else:
            bar.append("off", style=f"bold {COLORS['warning']}")

        return bar


# =============================================================================
# MODE TRANSITION DISPLAY
# =============================================================================


class ModeTransitionDisplay:
    """Display elegant mode transition messages.

    Visual output:
    ╭──────────────────────────────────────────────────────────────╮
    │  🔄 NORMAL → AUTO                                            │
    │  ────────────────────────────────────────────────────────    │
    │  ⚡ Auto-approve all tool executions                        │
    │                                                              │
    │  💡 Tools are auto-approved - I'll execute without asking   │
    │  ⚠️ I'll still explain what I'm doing                       │
    │  🛑 Press Ctrl+C to interrupt if needed                     │
    ╰──────────────────────────────────────────────────────────────╯
    """

    @staticmethod
    def render(
        old_mode: str, new_mode: str, new_emoji: str, description: str, tips: list[str]
    ) -> Panel:
        """Render mode transition panel."""
        content = Text()

        # Transition header
        content.append("🔄 ", style="bold")
        content.append(old_mode, style=COLORS["muted"])
        content.append(" → ", style=COLORS["muted"])
        content.append(new_mode, style=f"bold {COLORS['primary']}")
        content.append("\n")

        # Separator
        content.append("─" * 50, style=COLORS["secondary"])
        content.append("\n")

        # Mode description
        content.append(f"{new_emoji} ", style="bold")
        content.append(description, style=COLORS["text"])
        content.append("\n\n")

        # Tips
        for tip in tips[:3]:  # Max 3 tips
            content.append(f"  {tip}\n", style=COLORS["muted"])

        return Panel(content, border_style=COLORS["secondary"], padding=(0, 2))


# =============================================================================
# RESPONSE DISPLAY
# =============================================================================


class ResponseDisplay:
    """Display AI responses with elegant styling."""

    @staticmethod
    def render_response(content: RenderableType) -> Panel:
        """Wrap response content in a styled panel."""
        return Panel(
            content,
            title=f"[{COLORS['primary']}]👨‍🍳 Chef[/{COLORS['primary']}]",
            title_align="left",
            border_style=COLORS["secondary"],
            padding=(1, 2),
        )

    @staticmethod
    def render_tool_call(tool_name: str) -> Text:
        """Render a tool call indicator."""
        text = Text()
        text.append("  🔧 ", style="dim")
        text.append(tool_name, style=f"bold {COLORS['primary']}")
        return text

    @staticmethod
    def render_tool_result(success: bool = True, message: str = "") -> Text:
        """Render a tool result indicator."""
        text = Text()
        if success:
            text.append("    ✓", style=COLORS["success"])
        else:
            text.append("    ✗ ", style=COLORS["error"])
            if message:
                # Truncate long messages
                msg = message[:50] + "..." if len(message) > 50 else message
                text.append(msg, style=COLORS["error"])
        return text


# =============================================================================
# APPROVAL DIALOG
# =============================================================================


class ApprovalDialog:
    """Elegant tool approval dialog.

    Visual output:
    ╭──────────────────────────────────────────────────────────────╮
    │  🍽️ Order Confirmation                                       │
    │  ────────────────────────────────────────────────────────    │
    │  Tool: write_file                                            │
    │                                                              │
    │  {                                                           │
    │    "path": "/tmp/test.txt",                                  │
    │    "content": "Hello World"                                  │
    │  }                                                           │
    │                                                              │
    │  [Y] Execute  •  [n] Skip  •  [always] Auto-approve session │
    ╰──────────────────────────────────────────────────────────────╯
    """

    @staticmethod
    def render(tool_name: str, args_syntax: RenderableType) -> Panel:
        """Render the approval dialog."""
        content = Text()
        content.append("Tool: ", style="bold")
        content.append(tool_name, style=f"bold {COLORS['primary']}")

        # The args_syntax should be a Rich Syntax object passed separately
        combined = Group(content, Text(), args_syntax)

        subtitle = Text()
        subtitle.append("[", style=COLORS["muted"])
        subtitle.append("Y", style=f"bold {COLORS['success']}")
        subtitle.append("] Execute  •  [", style=COLORS["muted"])
        subtitle.append("n", style=f"bold {COLORS['error']}")
        subtitle.append("] Skip  •  [", style=COLORS["muted"])
        subtitle.append("always", style=f"bold {COLORS['warning']}")
        subtitle.append("] Auto-approve", style=COLORS["muted"])

        return Panel(
            combined,
            title=f"[{COLORS['primary']}]🍽️ Order Confirmation[/{COLORS['primary']}]",
            subtitle=subtitle,
            border_style=COLORS["primary"],
            padding=(1, 2),
        )


# =============================================================================
# HELP DISPLAY
# =============================================================================


class HelpDisplay:
    """Elegant help/commands display."""

    @staticmethod
    def render() -> Panel:
        """Render the help panel."""
        table = Table(show_header=False, box=None, padding=(0, 3))
        table.add_column("key", style=f"bold {COLORS['primary']}")
        table.add_column("desc", style=COLORS["text"])

        commands = [
            ("/help", "Show this help"),
            ("/model", "Switch AI model"),
            ("/modes", "List all available modes"),
            ("/clear", "Clear conversation history"),
            ("/status", "Show session status"),
            ("/stats", "Show session statistics"),
            ("/exit", "Exit ChefChat"),
            ("", ""),
            ("Shift+Tab", "Cycle through modes"),
            ("Ctrl+C", "Cancel current operation"),
            ("", ""),
            ("[dim]Chef's Specials[/dim]", ""),
            ("/chef", "Kitchen status report"),
            ("/wisdom", "Daily chef wisdom"),
            ("/roast", "Get roasted by Gordon"),
            ("/plate", "View current plating (stats)"),
            ("/fortune", "Developer fortune cookie"),
        ]

        for key, desc in commands:
            if key:
                table.add_row(key, desc)
            else:
                table.add_row("", "")

        return Panel(
            table,
            title=f"[{COLORS['primary']}]🍳 Commands[/{COLORS['primary']}]",
            border_style=COLORS["secondary"],
            padding=(1, 2),
        )


# =============================================================================
# FACTORY FUNCTIONS
# =============================================================================


def create_header(config: VibeConfig, mode_manager: ModeManager) -> Panel:
    """Factory function to create header from config and mode manager."""
    from vibe.core import __version__

    active_model = config.get_active_model()

    data = HeaderData(
        model=active_model.alias,  # Use .alias to get model name string
        mode_indicator=mode_manager.current_mode.value.upper(),
        mode_emoji=mode_manager.config.emoji,
        workdir=str(config.effective_workdir),
        version=__version__,
    )

    return HeaderDisplay(data).render()


def create_mode_transition(
    mode_manager: ModeManager, old_mode_name: str, new_mode_name: str, tips: list[str]
) -> Panel:
    """Factory function to create mode transition display."""
    return ModeTransitionDisplay.render(
        old_mode=old_mode_name,
        new_mode=new_mode_name,
        new_emoji=mode_manager.config.emoji,
        description=mode_manager.config.description,
        tips=tips,
    )


def get_greeting() -> tuple[str, str]:
    """Get a time-appropriate greeting.

    Returns:
        Tuple of (greeting_text, greeting_emoji)
    """
    from datetime import datetime

    hour = datetime.now().hour

    if 5 <= hour < 12:
        return ("Good morning", "☀️")
    elif 12 <= hour < 17:
        return ("Good afternoon", "🌤️")
    elif 17 <= hour < 21:
        return ("Good evening", "🌆")
    else:
        return ("Welcome back", "🌙")

