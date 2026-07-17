from __future__ import annotations

from typing import cast

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widget import Widget

from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_tool import FakeTool, FakeToolArgs, FakeToolResult
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.collapsible import CollapsibleSection
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage
from vibe.core.hooks.models import (
    HookEndEvent,
    HookMessageSeverity,
    HookRunEndEvent,
    HookRunStartEvent,
    HookStartEvent,
    HookType,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent


class ToolHooksApp(App):
    CSS_PATH = "../../vibe/cli/textual_ui/app.tcss"

    def __init__(self) -> None:
        super().__init__()
        self._scroll: VerticalScroll | None = None
        self._handler: EventHandler | None = None

    def compose(self) -> ComposeResult:
        self._scroll = VerticalScroll(id="messages")
        yield self._scroll

    def on_mount(self) -> None:
        async def mount_callback(
            widget: Widget, *, after: Widget | None = None, before: Widget | None = None
        ) -> None:
            if self._scroll is None:
                return
            if before is not None and before.parent is self._scroll:
                await self._scroll.mount(widget, before=before)
            elif after is not None and after.parent is self._scroll:
                await self._scroll.mount(widget, after=after)
            else:
                await self._scroll.mount(widget)

        self._handler = EventHandler(
            mount_callback=mount_callback, get_tools_collapsed=lambda: False
        )

    def freeze_spinners(self) -> None:
        for widget in self.query(ToolCallMessage):
            widget._is_spinning = False
            if widget._spinner_timer:
                widget._spinner_timer.stop()
                widget._spinner_timer = None
            widget._spinner.reset()
            if widget._indicator_widget:
                widget._indicator_widget.update(widget._spinner.current_frame())

    async def emit_tool_with_hooks(self) -> None:
        if self._handler is None:
            return
        call_id = "call_1"

        # pre_tool hooks
        await self._handler.handle_event(
            ToolCallEvent(
                tool_call_id=call_id,
                tool_name="stub_tool",
                tool_class=FakeTool,
                args=FakeToolArgs(),
            )
        )
        await self._handler.handle_event(
            HookRunStartEvent(
                scope=HookType.PRE_TOOL, tool_name="stub_tool", tool_call_id=call_id
            )
        )
        await self._handler.handle_event(HookStartEvent(hook_name="guard-bash"))
        await self._handler.handle_event(
            HookEndEvent(
                hook_name="guard-bash",
                status=HookMessageSeverity.OK,
                content="Command allowed",
                scope=HookType.PRE_TOOL,
                tool_call_id=call_id,
            )
        )
        await self._handler.handle_event(
            HookRunEndEvent(scope=HookType.PRE_TOOL, tool_call_id=call_id)
        )

        # tool result
        await self._handler.handle_event(
            ToolResultEvent(
                tool_name="stub_tool",
                tool_class=FakeTool,
                result=FakeToolResult(message="fake tool executed"),
                tool_call_id=call_id,
            )
        )

        # post_tool hooks
        await self._handler.handle_event(
            HookRunStartEvent(
                scope=HookType.POST_TOOL, tool_name="stub_tool", tool_call_id=call_id
            )
        )
        await self._handler.handle_event(HookStartEvent(hook_name="redact-secrets"))
        await self._handler.handle_event(
            HookEndEvent(
                hook_name="redact-secrets",
                status=HookMessageSeverity.WARNING,
                content="Replaced tool result (42 chars)",
                scope=HookType.POST_TOOL,
                tool_call_id=call_id,
            )
        )
        await self._handler.handle_event(
            HookRunEndEvent(scope=HookType.POST_TOOL, tool_call_id=call_id)
        )


def test_snapshot_tool_hooks(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        app = cast(ToolHooksApp, pilot.app)
        await app.emit_tool_with_hooks()
        await pilot.pause(0.3)
        app.freeze_spinners()
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_tool_hooks.py:ToolHooksApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_tool_hooks_expanded(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        app = cast(ToolHooksApp, pilot.app)
        await app.emit_tool_with_hooks()
        await pilot.pause(0.3)
        app.freeze_spinners()
        for section in app.query(CollapsibleSection):
            section.set_collapsed(False)
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_tool_hooks.py:ToolHooksApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )
