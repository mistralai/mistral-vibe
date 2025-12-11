"""ChefChat The Plate Widget - The Code Output Panel.

'The Plate' is where the finished dish is presented to the Head Chef.
This widget displays syntax-highlighted code output from the Line Cook.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rich.console import RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import RichLog, Static, TabbedContent, TabPane, TextArea

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


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


class ThePlate(TabbedContent):
    """The code output panel showing generated/modified code.

    Organized into tabs:
    - Code: The generated/modified code
    - Terminal: Output of execution/tests
    - Notes: Scratchpad or implementation notes
    """

    DEFAULT_CSS = """
    ThePlate {
        background: $secondary-bg;
        border: solid $panel-border;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 0;
    }

    ThePlate:focus {
        border: solid $accent;
    }

    #plate-code-scroll {
        padding: 1 2;
        background: $secondary-bg;
    }

    #plate-empty {
        color: $text-muted;
        text-style: italic;
        text-align: center;
        padding-top: 2;
    }

    .file-path {
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
        """Compose the tabbed interface."""
        with TabPane("Code", id="tab-code"):
            yield VerticalScroll(
                Static("🍽️ Waiting for the dish to be plated...", id="plate-empty"),
                id="plate-code-scroll",
            )

        with TabPane("Terminal", id="tab-terminal"):
            yield RichLog(id="plate-terminal", highlight=True, markup=True)

        with TabPane("Notes", id="tab-notes"):
            yield TextArea(language="markdown", id="plate-notes")

    def plate_code(
        self,
        code: str,
        language: str = "python",
        file_path: str | None = None,
        append: bool = False,
    ) -> CodeBlock:
        """Display code on the plate (Code Tab).

        Uses batch operations for better performance when clearing
        existing content.

        Args:
            code: The source code to display
            language: Programming language for highlighting
            file_path: Optional file path to show
            append: If True, append to existing. If False, replace.

        Returns:
            The created CodeBlock widget
        """
        scroll_container = self.query_one("#plate-code-scroll", VerticalScroll)

        with self.app.batch_update():
            # Remove empty state if present (using NoMatches exception)
            try:
                empty = scroll_container.query_one("#plate-empty", Static)
                empty.remove()
            except NoMatches:
                pass

            # If not appending, clear existing code blocks using batch operation
            if not append:
                # Collect all widgets to remove first (batch operation)
                widgets_to_remove: list[Static | CodeBlock] = []
                widgets_to_remove.extend(scroll_container.query(CodeBlock))
                widgets_to_remove.extend(scroll_container.query(".file-path"))

                # Then remove them all
                for widget in widgets_to_remove:
                    widget.remove()

            # Add file path label if provided
            if file_path:
                scroll_container.mount(Static(f"📄 {file_path}", classes="file-path"))

            # Create and mount the code block
            # Guard against empty file_path before string operations
            title: str | None = None
            if file_path:
                title = file_path.split("/")[-1] if "/" in file_path else file_path

            block = CodeBlock(code=code, language=language, title=title)
            scroll_container.mount(block)

            # Update reactive properties
            self.current_code = code
            self.current_language = language

            # Scroll to show new content
            scroll_container.scroll_end(animate=True)

        # Switch to Code tab
        self.active = "tab-code"

        return block

    def clear_plate(self) -> None:
        """Clear all code from the plate.

        Uses batch operations for better performance.
        """
        scroll_container = self.query_one("#plate-code-scroll", VerticalScroll)

        with self.app.batch_update():
            # Batch collect widgets to remove
            widgets_to_remove: list[Static | CodeBlock] = []
            widgets_to_remove.extend(scroll_container.query(CodeBlock))
            widgets_to_remove.extend(scroll_container.query(".file-path"))

            # Batch remove
            for widget in widgets_to_remove:
                widget.remove()

            # Restore empty state
            scroll_container.mount(
                Static("🍽️ Waiting for the dish to be plated...", id="plate-empty")
            )

            # Reset reactive properties
            self.current_code = ""
            self.current_language = "python"

            # Also clear terminal
            try:
                terminal = self.query_one("#plate-terminal", RichLog)
                terminal.clear()
            except NoMatches:
                logger.warning("Terminal widget not found during clear")

    def log_message(self, message: str) -> None:
        """Log a message to the Terminal tab.

        Args:
            message: The message to log
        """
        try:
            terminal = self.query_one("#plate-terminal", RichLog)
            terminal.write(message)
        except NoMatches:
            logger.warning("Terminal widget not found for logging")

    def get_notes(self) -> str:
        """Get the content of the Notes tab.

        Returns:
            The note content as a string
        """
        try:
            return self.query_one("#plate-notes", TextArea).text
        except NoMatches:
            return ""

    def set_notes(self, text: str) -> None:
        """Set the content of the Notes tab.

        Args:
            text: The text to set
        """
        try:
            self.query_one("#plate-notes", TextArea).text = text
        except NoMatches:
            logger.warning("Notes widget not found")

    def get_current_code(self) -> str:
        """Get the currently displayed code.

        Returns:
            The current code content
        """
        return self.current_code
