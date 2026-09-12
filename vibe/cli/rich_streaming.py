from __future__ import annotations

import sys
from typing import TextIO

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from vibe.app_server.events import (
    AppServerEvent,
    HistoryEntryAdded,
    HistoryEntryUpdated,
    StatsUpdated,
    TurnCompleted,
    TurnStarted,
)
from vibe.app_server.models import (
    CompletedEffectState,
    FailedEffectState,
    PendingEffectState,
    PublicEffectEntry,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicReasoningEntry,
    RunningEffectState,
)
from vibe.app_server.protocol import StatsUpdatedParams
from vibe.utils.tool_presentation import ToolEffectKind

TOOL_ICONS: dict[ToolEffectKind | str, str] = {
    ToolEffectKind.FILE_READ: "👀",
    ToolEffectKind.FILE_WRITE: "✍️",
    ToolEffectKind.FILE_EDIT: "📝",
    ToolEffectKind.FILE_SEARCH: "🔍",
    ToolEffectKind.SHELL: "⚡",
    ToolEffectKind.WEB_SEARCH: "🌐",
    ToolEffectKind.WEB_FETCH: "📥",
    ToolEffectKind.TODO: "📋",
    ToolEffectKind.SKILL: "🧠",
    ToolEffectKind.SUBAGENT: "🤖",
    ToolEffectKind.USER_QUESTION: "❓",
    ToolEffectKind.TOOL: "🔧",
}


def _format_tool_title(entry: PublicEffectEntry) -> tuple[str, str]:
    """Returns (icon, label) for a tool call."""
    detail = entry.detail
    icon = TOOL_ICONS.get(detail.kind, "🔧")

    # Try getting summary from display
    summary = ""
    if hasattr(detail, "display") and detail.display:
        if detail.display.summary:
            summary = detail.display.summary
        elif detail.display.message:
            summary = detail.display.message

    if not summary:
        # Fallback based on input
        inp = getattr(detail, "input", None)
        if inp is not None:
            if hasattr(inp, "file_path"):
                summary = inp.file_path
            elif hasattr(inp, "command"):
                summary = inp.command
            elif hasattr(inp, "query"):
                summary = inp.query
            elif hasattr(inp, "path"):
                summary = inp.path
            elif hasattr(inp, "pattern"):
                summary = inp.pattern
            elif hasattr(inp, "name"):
                summary = inp.name
            else:
                summary = str(inp)

    tool_name = getattr(detail, "tool_name", detail.kind.value)
    if summary:
        label = f"{tool_name}: {summary}"
    else:
        label = tool_name

    return icon, label


class VisualStreamingOutput:
    """Renders live streaming text, tool execution badges, and cost/token stats
    using Rich without taking over the full terminal screen.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout
        self._console = Console(file=self._stream, force_terminal=True, highlight=False)
        self._live: Live | None = None

        # State tracking
        self._active_message_id: str | None = None
        self._current_text: str = ""
        self._status_text: str = "Thinking..."
        self._model_name: str = ""

        # Track active and finished tools
        self._active_tools: dict[str, PublicEffectEntry] = {}
        self._finished_tools: set[str] = set()

        # Stats tracking
        self._stats: StatsUpdatedParams | None = None
        self._turn_count: int = 0
        self._is_active: bool = False

    def set_model_name(self, model_name: str) -> None:
        self._model_name = model_name

    def start(self, history: list[PublicHistoryEntry]) -> None:
        self._is_active = True
        self._live = Live(
            self._render_view(),
            console=self._console,
            refresh_per_second=12,
            transient=False,
            auto_refresh=True,
        )
        self._live.start()

    def _render_view(self) -> Group:
        elements = []

        # 1. Main response streaming markdown or text
        if self._current_text:
            elements.append(Markdown(self._current_text))

        # 2. Active spinner / status line if we're generating or running tools
        if self._is_active:
            status_line = Text()
            if self._active_tools:
                # Show running tool info in spinner
                first_active = next(iter(self._active_tools.values()))
                icon, label = _format_tool_title(first_active)
                status_line.append(f" {icon} Running {label}...", style="bold cyan")
            else:
                status_line.append(f" {self._status_text}", style="dim italic cyan")
            elements.append(Spinner("dots", text=status_line))

        return (
            Group(*elements)
            if elements
            else Group(
                Spinner("dots", text=Text(" Thinking...", style="dim italic cyan"))
            )
        )

    def _print_tool_badge(self, entry: PublicEffectEntry, success: bool = True) -> None:
        icon, label = _format_tool_title(entry)
        text = Text()
        text.append(f"  {icon} ", style="bold")
        text.append(label, style="bold cyan")

        if success:
            text.append("  ✔", style="bold green")
        else:
            text.append("  ✖ failed", style="bold red")

        # Temporarily pause live display to cleanly print badge above it
        if self._live:
            self._live.console.print(text)
        else:
            self._console.print(text)

    def consume(self, event: AppServerEvent) -> None:  # noqa: PLR0912
        match event:
            case TurnStarted():
                self._turn_count += 1
                self._status_text = "Thinking..."
                if self._live:
                    self._live.update(self._render_view())

            case HistoryEntryAdded(entry=entry) | HistoryEntryUpdated(entry=entry):
                if isinstance(entry, PublicMessageEntry):
                    if entry.role == "assistant":
                        self._active_message_id = entry.id
                        self._current_text = entry.text or ""
                        if self._live:
                            self._live.update(self._render_view())

                elif isinstance(entry, PublicReasoningEntry):
                    self._status_text = "Reasoning..."
                    if self._live:
                        self._live.update(self._render_view())

                elif isinstance(entry, PublicEffectEntry):
                    entry_id = entry.id
                    state = entry.state

                    if isinstance(state, (PendingEffectState, RunningEffectState)):
                        self._active_tools[entry_id] = entry
                        if self._live:
                            self._live.update(self._render_view())

                    elif isinstance(state, CompletedEffectState):
                        self._active_tools.pop(entry_id, None)
                        if entry_id not in self._finished_tools:
                            self._finished_tools.add(entry_id)
                            self._print_tool_badge(entry, success=True)
                        if self._live:
                            self._live.update(self._render_view())

                    elif isinstance(state, FailedEffectState):
                        self._active_tools.pop(entry_id, None)
                        if entry_id not in self._finished_tools:
                            self._finished_tools.add(entry_id)
                            self._print_tool_badge(entry, success=False)
                        if self._live:
                            self._live.update(self._render_view())

            case StatsUpdated(params=params):
                self._stats = params

            case TurnCompleted():
                self._status_text = "Completed"
                if self._live:
                    self._live.update(self._render_view())

            case _:
                pass

    def finalize(self, history: list[PublicHistoryEntry]) -> str | None:
        self._is_active = False
        if self._live:
            # Update to final view (without spinner)
            self._live.update(
                Group(Markdown(self._current_text)) if self._current_text else Text("")
            )
            self._live.stop()
            self._live = None

        # Print summary panel with detailed stats
        self._print_summary_panel()
        return self._current_text

    def _print_summary_panel(self) -> None:
        if not self._stats:
            return

        s = self._stats.stats
        total_tokens = s.session_total_llm_tokens
        prompt_tokens = s.session_prompt_tokens
        comp_tokens = s.session_completion_tokens
        cached_tokens = s.session_cached_tokens

        # Calculate cost
        input_cost = (prompt_tokens / 1_000_000.0) * s.input_price_per_million
        output_cost = (comp_tokens / 1_000_000.0) * s.output_price_per_million
        total_cost = input_cost + output_cost

        cost_str = f"${total_cost:.4f}"
        tools_str = f"{len(self._finished_tools)}"

        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim", justify="right")
        grid.add_column(style="bold")
        grid.add_column(style="dim", justify="right")
        grid.add_column(style="bold")

        # Row 1: Model & Cost
        if self._model_name:
            grid.add_row(
                "Model:",
                f"[bold yellow]{self._model_name}[/bold yellow]",
                "Cost:",
                f"[bold green]{cost_str}[/bold green]",
            )
        else:
            grid.add_row(
                "Cost:",
                f"[bold green]{cost_str}[/bold green]",
                "Tools:",
                f"[bold magenta]{tools_str}[/bold magenta]",
            )

        # Row 2: Tokens breakdown & Tool Calls
        tokens_breakdown = (
            f"[bold cyan]{total_tokens:,}[/bold cyan] "
            f"([dim]In:[/dim] [cyan]{prompt_tokens:,}[/cyan] | "
            f"[dim]Out:[/dim] [cyan]{comp_tokens:,}[/cyan] | "
            f"[dim]Cached:[/dim] [cyan]{cached_tokens:,}[/cyan])"
        )

        if self._model_name:
            grid.add_row(
                "Tokens:",
                tokens_breakdown,
                "Tools:",
                f"[bold magenta]{tools_str}[/bold magenta]",
            )
        else:
            grid.add_row("Tokens:", tokens_breakdown, "", "")

        panel = Panel(
            grid,
            title="[bold green]Session Summary[/bold green]",
            border_style="dim green",
            expand=False,
        )
        self._console.print(panel)
