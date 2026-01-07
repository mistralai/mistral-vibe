from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.messages import ExpandingBorder
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.status_message import StatusMessage
from vibe.cli.textual_ui.widgets.tool_widgets import get_result_widget
from vibe.cli.textual_ui.widgets.utils import DEFAULT_TOOL_SHORTCUT, TOOL_SHORTCUTS
from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.types import ToolCallEvent, ToolResultEvent


class ToolCallMessage(StatusMessage):
    def __init__(
        self, event: ToolCallEvent | None = None, *, tool_name: str | None = None
    ) -> None:
        if event is None and tool_name is None:
            raise ValueError("Either event or tool_name must be provided")

        self._event = event
        self._tool_name = tool_name or (event.tool_name if event else "unknown")
        self._is_history = event is None

        super().__init__()
        self.add_class("tool-call")

        if self._is_history:
            self._is_spinning = False

    def get_content(self) -> str:
        if self._event and self._event.tool_class:
            adapter = ToolUIDataAdapter(self._event.tool_class)
            display = adapter.get_call_display(self._event)
            return display.summary
        return self._tool_name


class ToolResultMessage(Static):
    def __init__(
        self,
        event: ToolResultEvent | None = None,
        call_widget: ToolCallMessage | None = None,
        collapsed: bool = True,
        *,
        tool_name: str | None = None,
        content: str | None = None,
    ) -> None:
        if event is None and tool_name is None:
            raise ValueError("Either event or tool_name must be provided")

        self._event = event
        self._call_widget = call_widget
        self._tool_name = tool_name or (event.tool_name if event else "unknown")
        self._content = content
        self.collapsed = collapsed
        self._content_container: Vertical | None = None

        super().__init__()
        self.add_class("tool-result")

    @property
    def tool_name(self) -> str:
        return self._tool_name

    def _shortcut(self) -> str:
        return TOOL_SHORTCUTS.get(self._tool_name, DEFAULT_TOOL_SHORTCUT)

    def _hint(self) -> str:
        action = "expand" if self.collapsed else "collapse"
        return f"({self._shortcut()} to {action})"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="tool-result-container"):
            yield ExpandingBorder(classes="tool-result-border")
            self._content_container = Vertical(classes="tool-result-content")
            yield self._content_container

    async def on_mount(self) -> None:
        if self._call_widget:
            success = self._event is None or (
                not self._event.error and not self._event.skipped
            )
            self._call_widget.stop_spinning(success=success)
        await self._render_result()

    async def _render_result(self) -> None:
        if self._content_container is None:
            return

        await self._content_container.remove_children()

        if self._event is None:
            await self._render_simple()
            return

        if self._event.error:
            self.add_class("error-text")
            if self.collapsed:
                await self._content_container.mount(
                    NoMarkupStatic(f"Error. {self._hint()}")
                )
            else:
                await self._content_container.mount(
                    NoMarkupStatic(f"Error: {self._event.error}")
                )
            return

        if self._event.skipped:
            self.add_class("warning-text")
            reason = self._event.skip_reason or "User skipped"
            if self.collapsed:
                await self._content_container.mount(
                    NoMarkupStatic(f"Skipped. {self._hint()}")
                )
            else:
                await self._content_container.mount(
                    NoMarkupStatic(f"Skipped: {reason}")
                )
            return

        self.remove_class("error-text")
        self.remove_class("warning-text")

        if self._event.tool_class is None:
            await self._render_simple()
            return

        adapter = ToolUIDataAdapter(self._event.tool_class)
        display = adapter.get_result_display(self._event)

        widget = get_result_widget(
            self._event.tool_name,
            self._event.result,
            success=display.success,
            message=display.message,
            collapsed=self.collapsed,
            warnings=display.warnings,
        )
        await self._content_container.mount(widget)

    async def _render_simple(self) -> None:
        if self._content_container is None:
            return

        if self.collapsed:
            await self._content_container.mount(
                NoMarkupStatic(f"{self._tool_name} completed {self._hint()}")
            )
            return

        if self._content:
            await self._content_container.mount(NoMarkupStatic(self._content))
        else:
            await self._content_container.mount(
                NoMarkupStatic(f"{self._tool_name} completed.")
            )

    async def set_collapsed(self, collapsed: bool) -> None:
        if self.collapsed == collapsed:
            return
        self.collapsed = collapsed
        await self._render_result()

    async def toggle_collapsed(self) -> None:
        self.collapsed = not self.collapsed
        await self._render_result()
