from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.collapsible import (
    ClickWithoutDragMixin,
    CollapsibleSection,
    lines_label,
)
from vibe.cli.textual_ui.widgets.messages import ExpandingBorder
from vibe.cli.textual_ui.widgets.no_markup_static import (
    NoMarkupStatic,
    NonSelectableStatic,
)
from vibe.cli.textual_ui.widgets.status_message import StatusMessage
from vibe.cli.textual_ui.widgets.tool_widgets import ToolResultWidget, get_result_widget
from vibe.core.tools.ui import ToolCallDisplay, ToolUIDataAdapter
from vibe.core.types import ToolCallEvent, ToolResultEvent


class ToolCallMessage(StatusMessage):
    def __init__(
        self, event: ToolCallEvent | None = None, *, tool_name: str | None = None
    ) -> None:
        if event is None and tool_name is None:
            raise ValueError("Either event or tool_name must be provided")

        self._event = event
        self._tool_name = tool_name or (event.tool_name if event else None) or "unknown"
        self._is_history = event is None
        self._stream_widget: NoMarkupStatic | None = None
        self._suffix_widget: NoMarkupStatic | None = None

        super().__init__()
        self.add_class("tool-call")

        if self._is_history:
            self._is_spinning = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="tool-call-container"):
            with Horizontal(classes="tool-call-header"):
                self._indicator_widget = NonSelectableStatic(
                    self._spinner.current_frame(), classes="status-indicator-icon"
                )
                yield self._indicator_widget
                self._text_widget = NoMarkupStatic("", classes="status-indicator-text")
                yield self._text_widget
                self._suffix_widget = NoMarkupStatic(
                    "", classes="status-indicator-suffix"
                )
                self._suffix_widget.display = False
                yield self._suffix_widget
            self._stream_widget = NoMarkupStatic("", classes="tool-stream-message")
            self._stream_widget.display = False
            yield self._stream_widget

    def on_mount(self) -> None:
        super().on_mount()
        siblings = list(self.parent.children) if self.parent else []
        idx = siblings.index(self) if self in siblings else -1
        if idx > 0 and isinstance(
            siblings[idx - 1], (ToolCallMessage, ToolResultMessage)
        ):
            self.add_class("no-gap")

    @property
    def tool_call_id(self) -> str | None:
        return self._event.tool_call_id if self._event else None

    def get_content(self) -> str:
        return self._call_display().summary

    def get_content_suffix(self) -> str:
        return self._call_display().suffix

    def _call_display(self) -> ToolCallDisplay:
        if self._event:
            adapter = ToolUIDataAdapter(self._event.tool_class)
            return adapter.get_call_display(self._event)
        return ToolCallDisplay(summary=self._tool_name)

    def update_event(self, event: ToolCallEvent) -> None:
        self._event = event
        self._tool_name = event.tool_name
        self._set_text(self.get_content(), self.get_content_suffix())

    def set_stream_message(self, message: str) -> None:
        """Update the stream message displayed below the tool call indicator."""
        if self._stream_widget:
            self._stream_widget.update(f"→ {message}")
            self._stream_widget.display = True

    def stop_spinning(self, success: bool = True) -> None:
        """Stop the spinner while keeping stream row stable to avoid layout jumps."""
        super().stop_spinning(success)

    def set_result_text(self, text: str, suffix: str = "") -> None:
        self._set_text(text, suffix)

    def _set_text(self, text: str, suffix: str) -> None:
        if self._text_widget:
            self._text_widget.update(text)
        self._update_suffix(suffix)

    def _update_suffix(self, suffix: str) -> None:
        if self._suffix_widget:
            self._suffix_widget.update(suffix)
            self._suffix_widget.display = bool(suffix)

    def update_display(self) -> None:
        super().update_display()
        self._update_suffix(self.get_content_suffix())


class ToolResultMessage(ClickWithoutDragMixin, Static):
    def __init__(
        self,
        event: ToolResultEvent | None = None,
        call_widget: ToolCallMessage | None = None,
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
        self._content_container: Vertical | None = None
        self._result_widget: ToolResultWidget | None = None

        super().__init__()
        self.add_class("tool-result")

    @property
    def tool_name(self) -> str:
        return self._tool_name

    def compose(self) -> ComposeResult:
        with Horizontal(classes="tool-result-container"):
            self._border = ExpandingBorder(classes="tool-result-border")
            yield self._border
            self._content_container = Vertical(classes="tool-result-content")
            yield self._content_container

    async def on_mount(self) -> None:
        if self._call_widget:
            success = self._determine_success()
            self._call_widget.stop_spinning(success=success)
            result_text, result_suffix = self._get_result_text()
            self._call_widget.set_result_text(result_text, result_suffix)
        await self._render_result()

    def _determine_success(self) -> bool:
        if self._event is None:
            return True
        if self._event.error or self._event.skipped:
            return False
        if self._event.tool_class:
            adapter = ToolUIDataAdapter(self._event.tool_class)
            display = adapter.get_result_display(self._event)
            return display.success
        return True

    def _get_result_text(self) -> tuple[str, str]:
        if self._event is None:
            return f"{self._tool_name} completed", ""

        if self._event.error:
            return f"{self._tool_name}: error", ""

        if self._event.skipped:
            return f"{self._tool_name}: skipped", ""

        if self._event.tool_class:
            adapter = ToolUIDataAdapter(self._event.tool_class)
            display = adapter.get_result_display(self._event)
            return display.message, display.suffix

        return f"{self._tool_name} completed", ""

    async def _render_result(self) -> None:
        if self._content_container is None:
            return

        await self._content_container.remove_children()

        if self._event is None:
            if not self._content:
                self.display = False
                return
            line_count = len(self._content.strip("\n").split("\n"))
            await self._content_container.mount(
                CollapsibleSection(
                    NoMarkupStatic(self._content, classes="tool-result-detail"),
                    collapsed_label=lines_label(line_count),
                )
            )
            self.display = True
            return

        if self._event.error:
            self.add_class("error-text")
            error_text = f"Error: {self._event.error}"
            line_count = len(error_text.strip("\n").split("\n"))
            await self._content_container.mount(
                CollapsibleSection(
                    NoMarkupStatic(error_text), collapsed_label=lines_label(line_count)
                )
            )
            self.display = True
            return

        if self._event.skipped:
            self.add_class("warning-text")
            reason = self._event.skip_reason or "User skipped"
            await self._content_container.mount(NoMarkupStatic(f"Skipped: {reason}"))
            self.display = True
            return

        self.remove_class("error-text")
        self.remove_class("warning-text")

        if self._event.tool_class is None:
            self.display = False
            return

        adapter = ToolUIDataAdapter(self._event.tool_class)
        display = adapter.get_result_display(self._event)

        widget = get_result_widget(
            self._event.tool_name,
            self._event.result,
            success=display.success,
            message=display.message,
            warnings=display.warnings,
        )
        await self._content_container.mount(widget)
        self._result_widget = widget
        self._apply_border_colors(collapsed=True)
        self.display = bool(widget.children)

    def _apply_border_colors(self, *, collapsed: bool) -> None:
        if self._result_widget is None:
            return
        colors = self._result_widget.border_row_colors
        if collapsed:
            preview = self._result_widget.PREVIEW_LINES
            colors = {i: c for i, c in colors.items() if i < preview}
        self._border.set_row_colors(colors)

    def on_collapsible_section_toggled(
        self, message: CollapsibleSection.Toggled
    ) -> None:
        self._apply_border_colors(collapsed=message.is_collapsed)

    async def on_click(self, event: events.Click) -> None:
        if self._click_is_passive(event):
            return
        sections = list(self.query(CollapsibleSection))
        if sections:
            sections[0].toggle()
