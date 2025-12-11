"""ChefChat Centralized Error Handler
=====================================

Provides consistent, beautiful error handling using Rich formatting.
All errors across ChefChat should use this handler for visual consistency.

Usage:
    from vibe.core.error_handler import ChefErrorHandler

    try:
        risky_operation()
    except Exception as e:
        ChefErrorHandler.display_error(e, context="API Call", show_traceback=True)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.traceback import Traceback

if TYPE_CHECKING:
    pass

# Shared console instance
_console = Console()

# Color constants (matching ui_components.py)
COLORS = {
    "error": "#FF4444",
    "warning": "#FFB800",
    "info": "#00D26A",
    "muted": "#666666",
    "primary": "#FF7000",
}


class ChefErrorHandler:
    """Centralized error handling with Rich formatting.

    Provides consistent error display across ChefChat:
    - Formatted error panels with context
    - Optional traceback display
    - Warning panels for non-critical issues
    - Info panels for important notices

    Example:
        >>> try:
        ...     riskyOperation()
        ... except Exception as e:
        ...     ChefErrorHandler.display_error(e, "File Operation")
    """

    @staticmethod
    def display_error(
        error: Exception,
        context: str = "Operation",
        show_traceback: bool = False,
        console: Console | None = None,
    ) -> None:
        """Display a formatted error panel.

        Args:
            error: The exception that occurred
            context: Description of what was happening (e.g., "API Call", "File Write")
            show_traceback: Whether to show the full traceback
            console: Optional custom console (uses default if not provided)
        """
        con = console or _console

        # Build error content
        content = Text()
        content.append(f"{type(error).__name__}\n", style=f"bold {COLORS['error']}")

        # Check if this is a BackendError for special formatting
        try:
            from vibe.core.llm.exceptions import BackendError

            if isinstance(error, BackendError):
                # Format BackendError with structured details
                content.append("API Error Details:\n\n", style=f"bold {COLORS['warning']}")

                # Status and basic info
                if error.status:
                    content.append(f"Status: ", style=COLORS["muted"])
                    content.append(f"{error.status}\n", style=COLORS["error"])

                content.append(f"Model: ", style=COLORS["muted"])
                content.append(f"{error.model}\n", style="bold")

                content.append(f"Provider: ", style=COLORS["muted"])
                content.append(f"{error.provider}\n\n", style="bold")

                # Provider message
                if error.parsed_error:
                    content.append(f"Message: ", style=COLORS["muted"])
                    content.append(f"{error.parsed_error}\n\n", style=COLORS["error"])

                # Payload summary
                content.append("Request Summary:\n", style=f"bold {COLORS['info']}")
                content.append(f"  Messages: ", style=COLORS["muted"])
                content.append(f"{error.payload_summary.message_count}\n")
                content.append(f"  Approx chars: ", style=COLORS["muted"])
                content.append(f"{error.payload_summary.approx_chars:,}\n")
                content.append(f"  Temperature: ", style=COLORS["muted"])
                content.append(f"{error.payload_summary.temperature}\n")

                # Body excerpt if available
                if error.body_text:
                    content.append(f"\nResponse excerpt:\n", style=COLORS["muted"])
                    excerpt = error._excerpt(error.body_text, n=200)
                    content.append(f"{excerpt}\n", style="dim")
            else:
                # Standard error formatting
                content.append(str(error), style=COLORS["muted"])
        except ImportError:
            # Fallback if BackendError not available
            content.append(str(error), style=COLORS["muted"])

        # Create panel
        error_panel = Panel(
            content,
            title=f"[{COLORS['error']}]❌ {context} Failed[/{COLORS['error']}]",
            border_style=COLORS["error"],
            padding=(1, 2),
        )
        con.print()
        con.print(error_panel)

        # Show traceback if requested
        if show_traceback and error.__traceback__:
            con.print()
            con.print(
                Traceback.from_exception(
                    type(error),
                    error,
                    error.__traceback__,
                    show_locals=False,
                    max_frames=10,
                )
            )

    @staticmethod
    def display_warning(
        message: str, context: str = "Warning", console: Console | None = None
    ) -> None:
        """Display a formatted warning panel.

        Args:
            message: The warning message
            context: Description of the warning context
            console: Optional custom console
        """
        con = console or _console

        warning_panel = Panel(
            Text(message, style=COLORS["muted"]),
            title=f"[{COLORS['warning']}]⚠️  {context}[/{COLORS['warning']}]",
            border_style=COLORS["warning"],
            padding=(0, 2),
        )
        con.print()
        con.print(warning_panel)

    @staticmethod
    def display_info(
        message: str, context: str = "Info", console: Console | None = None
    ) -> None:
        """Display a formatted info panel.

        Args:
            message: The info message
            context: Description of the info context
            console: Optional custom console
        """
        con = console or _console

        info_panel = Panel(
            Text(message, style=COLORS["muted"]),
            title=f"[{COLORS['info']}]ℹ️  {context}[/{COLORS['info']}]",
            border_style=COLORS["info"],
            padding=(0, 2),
        )
        con.print()
        con.print(info_panel)

    @staticmethod
    def format_error_message(error: Exception, context: str = "Error") -> str:
        """Format an error for logging or display without Rich markup.

        Args:
            error: The exception
            context: Context description

        Returns:
            Plain text error message
        """
        return f"[{context}] {type(error).__name__}: {error}"


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = ["COLORS", "ChefErrorHandler"]
