"""ChefChat The Plate Widget - The Code Output Panel.

'The Plate' is where the finished dish is presented to the Head Chef.
This widget displays syntax-highlighted code output from the Line Cook.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static

if TYPE_CHECKING:
    pass


class CodeBlock(Static):
    """A syntax-highlighted code block."""

    DEFAULT_CSS = """
    CodeBlock {
        background: $primary-bg;
        border: round $panel-border;
        padding: 1;
        margin: 1 0;
    }
    """

    def __init__(
        self,
        code: str,
        language: str = "python",
        title: str | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize a code block.

        Args:
            code: The source code to display
            language: Programming language for syntax highlighting
            title: Optional title for the code block
            name: Widget name
            id: Widget ID
            classes: CSS classes
        """
        super().__init__(name=name, id=id, classes=classes)
        self.code = code
        self.language = language
        self.title = title

    def render(self) -> RenderableType:
        """Render the syntax-highlighted code."""
        syntax = Syntax(
            self.code,
            self.language,
            theme="monokai",
            line_numbers=True,
            word_wrap=True,
            background_color="#1a1b26",
        )

        if self.title:
            return Panel(
                syntax, title=self.title, title_align="left", border_style="dim"
            )
        return syntax


class ThePlate(VerticalScroll):
    """The code output panel showing generated/modified code.

    Supports multiple code blocks with different languages
    and optional file path indicators.
    """

    DEFAULT_CSS = """
    ThePlate {
        background: $secondary-bg;
        border: solid $panel-border;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    ThePlate:focus {
        border: solid $accent;
    }

    ThePlate #plate-empty {
        color: $text-muted;
        text-style: italic;
        text-align: center;
        padding-top: 2;
    }

    ThePlate .file-path {
        color: $info;
        text-style: bold;
        margin-bottom: 0;
    }
    """

    BORDER_TITLE = "🍽️ The Plate"

    # Track the current code content
    current_code: reactive[str] = reactive("", init=False)
    current_language: reactive[str] = reactive("python", init=False)

    def compose(self) -> ComposeResult:
        """Compose the initial empty state."""
        yield Static("🍽️ Waiting for the dish to be plated...", id="plate-empty")

    def plate_code(
        self,
        code: str,
        language: str = "python",
        file_path: str | None = None,
        append: bool = False,
    ) -> CodeBlock:
        """Display code on the plate.

        Args:
            code: The source code to display
            language: Programming language for highlighting
            file_path: Optional file path to show
            append: If True, append to existing. If False, replace.

        Returns:
            The created CodeBlock widget
        """
        # Remove empty state if present
        try:
            empty = self.query_one("#plate-empty", Static)
            empty.remove()
        except Exception:
            pass

        # If not appending, clear existing code blocks
        if not append:
            for block in self.query(CodeBlock):
                block.remove()
            for label in self.query(".file-path"):
                label.remove()

        # Add file path label if provided
        if file_path:
            self.mount(Static(f"📄 {file_path}", classes="file-path"))

        # Create and mount the code block
        title = file_path.split("/")[-1] if file_path else None
        block = CodeBlock(code=code, language=language, title=title)
        self.mount(block)

        # Update reactive properties
        self.current_code = code
        self.current_language = language

        # Scroll to show new content
        self.scroll_end(animate=True)

        return block

    def clear_plate(self) -> None:
        """Clear all code from the plate."""
        # Remove all code blocks and file paths
        for block in self.query(CodeBlock):
            block.remove()
        for label in self.query(".file-path"):
            label.remove()

        # Restore empty state
        self.mount(Static("🍽️ Waiting for the dish to be plated...", id="plate-empty"))

        # Reset reactive properties
        self.current_code = ""
        self.current_language = "python"

    def get_current_code(self) -> str:
        """Get the currently displayed code."""
        return self.current_code
