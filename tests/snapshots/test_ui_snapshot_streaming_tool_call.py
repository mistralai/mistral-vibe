from __future__ import annotations

from typing import cast
from unittest.mock import patch

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.pilot import Pilot

from tests.snapshots.snap_compare import SnapCompare
from vibe.cli.textual_ui.widgets.status_message import StatusMessage
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage
from vibe.core.tools.builtins.read_file import ReadFile, ReadFileArgs
from vibe.core.types import ToolCallEvent


class ToolCallStreamingUpdateTest(App):
    CSS_PATH = "../../vibe/cli/textual_ui/app.tcss"

    def __init__(self) -> None:
        super().__init__()
        self._widget: ToolCallMessage | None = None

    def compose(self) -> ComposeResult:
        partial_event = ToolCallEvent(
            tool_call_id="tc_streaming",
            tool_call_index=0,
            tool_name="read_file",
            tool_class=ReadFile,
            args=None,
        )
        self._widget = ToolCallMessage(partial_event)

        with VerticalScroll():
            yield self._widget

    def update_with_full_event(self) -> None:
        if self._widget is None:
            return

        full_event = ToolCallEvent(
            tool_call_id="tc_streaming",
            tool_call_index=0,
            tool_name="read_file",
            tool_class=ReadFile,
            args=ReadFileArgs(path="/test/example.py"),
        )
        self._widget.update_event(full_event)


def test_snapshot_tool_call_partial(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)

    with patch.object(StatusMessage, "start_spinner_timer"):
        assert snap_compare(
            "test_ui_snapshot_streaming_tool_call.py:ToolCallStreamingUpdateTest",
            terminal_size=(80, 10),
            run_before=run_before,
        )


def test_snapshot_tool_call_updated(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        app = cast(ToolCallStreamingUpdateTest, pilot.app)
        app.update_with_full_event()
        await pilot.pause(0.1)

    with patch.object(StatusMessage, "start_spinner_timer"):
        assert snap_compare(
            "test_ui_snapshot_streaming_tool_call.py:ToolCallStreamingUpdateTest",
            terminal_size=(80, 10),
            run_before=run_before,
        )
