from __future__ import annotations

from textual.widgets import Static

from vibe.cli.textual_ui.renderers import get_renderer
from vibe.cli.textual_ui.widgets.blinking_message import BlinkingMessage
from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.types import ToolCallEvent, ToolResultEvent


class ToolCallMessage(BlinkingMessage):
    def __init__(self, event: ToolCallEvent) -> None:
        self.event = event
        super().__init__()
        self.add_class("tool-call")

    def get_content(self) -> str:
        if not self.event.tool_class:
            return f"{self.event.tool_name}"

        adapter = ToolUIDataAdapter(self.event.tool_class)
        display = adapter.get_call_display(self.event)

        return f"{display.summary}"


class ToolResultMessage(Static):
    def __init__(
        self,
        event: ToolResultEvent,
        call_widget: ToolCallMessage | None = None,
        collapsed: bool = True,
    ) -> None:
        self.event = event
        self.call_widget = call_widget
        self.collapsed = collapsed

        super().__init__()
        self.add_class("tool-result")

    async def on_mount(self) -> None:
        if self.call_widget:
            success = not self.event.error and not self.event.skipped
            self.call_widget.stop_blinking(success=success)
        await self.render_result()

    async def render_result(self) -> None:
        await self.remove_children()

        if self.event.error:
            self.add_class("error-text")
            if self.collapsed:
                self.update("Error. (ctrl+o to expand)")
            else:
                await self.mount(Static(f"Error: {self.event.error}", markup=False))
            return

        if self.event.skipped:
            self.add_class("warning-text")
            reason = self.event.skip_reason or "User skipped"
            if self.collapsed:
                self.update("Skipped. (ctrl+o to expand)")
            else:
                await self.mount(Static(f"Skipped: {reason}", markup=False))
            return

        self.remove_class("error-text")
        self.remove_class("warning-text")

        adapter = ToolUIDataAdapter(self.event.tool_class)
        display = adapter.get_result_display(self.event)

        renderer = get_renderer(self.event.tool_name)
        widget_class, data = renderer.get_result_widget(display, self.collapsed)

        result_widget = widget_class(data, collapsed=self.collapsed)
        await self.mount(result_widget)

    async def set_collapsed(self, collapsed: bool) -> None:
        if self.collapsed != collapsed:
            self.collapsed = collapsed
            await self.render_result()

    async def toggle_collapsed(self) -> None:
        self.collapsed = not self.collapsed
        await self.render_result()
