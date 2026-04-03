from __future__ import annotations

from typing import cast

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widget import Widget

from tests.snapshots.snap_compare import SnapCompare
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage
from vibe.core.tools.builtins.read_file import ReadFile, ReadFileArgs, ReadFileResult
from vibe.core.types import ToolCallEvent, ToolResultEvent


class ParallelToolCallsApp(App):
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
            widget: Widget, *, after: Widget | None = None
        ) -> None:
            if self._scroll is None:
                return
            if after is not None and after.parent is self._scroll:
                await self._scroll.mount(widget, after=after)
            else:
                await self._scroll.mount(widget)

        self._handler = EventHandler(
            mount_callback=mount_callback, get_tools_collapsed=lambda: False
        )

    async def emit_all_tool_calls(self) -> None:
        if self._handler is None:
            return
        for i in range(3):
            await self._handler.handle_event(
                ToolCallEvent(
                    tool_call_id=f"tc_{i}",
                    tool_call_index=i,
                    tool_name="read_file",
                    tool_class=ReadFile,
                    args=ReadFileArgs(path=f"/src/file_{i}.py"),
                )
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

    async def resolve_all_results(self) -> None:
        if self._handler is None:
            return
        for i in range(3):
            await self._handler.handle_event(
                ToolResultEvent(
                    tool_name="read_file",
                    tool_class=ReadFile,
                    result=ReadFileResult(
                        path=f"/src/file_{i}.py",
                        content=f"# content of file_{i}.py",
                        lines_read=1,
                        was_truncated=False,
                    ),
                    tool_call_id=f"tc_{i}",
                )
            )


def test_snapshot_parallel_tool_calls_pending(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        app = cast(ParallelToolCallsApp, pilot.app)
        await app.emit_all_tool_calls()
        await pilot.pause(0.3)
        app.freeze_spinners()
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_parallel_tool_calls.py:ParallelToolCallsApp",
        terminal_size=(80, 15),
        run_before=run_before,
    )


def test_snapshot_parallel_tool_calls_resolved(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        app = cast(ParallelToolCallsApp, pilot.app)
        await app.emit_all_tool_calls()
        await pilot.pause(0.3)
        await app.resolve_all_results()
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_parallel_tool_calls.py:ParallelToolCallsApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )
