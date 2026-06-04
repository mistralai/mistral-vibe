from __future__ import annotations

from textual.containers import VerticalScroll
from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.core.tools.builtins.write_file import WriteFileArgs

WF_CONTENT_LONG = "\n".join(f"line_{i:03d} = {i * 7}" for i in range(1, 101))
WF_CONTENT_SHORT = "line_001 = 7"


class WriteApprovalLongContentApp(BaseSnapshotTestApp):
    async def on_ready(self) -> None:
        args = WriteFileArgs(path="src/example.py", content=WF_CONTENT_LONG)
        await self._switch_to_approval_app("write_file", args)


class WriteApprovalShortContentApp(BaseSnapshotTestApp):
    async def on_ready(self) -> None:
        args = WriteFileArgs(path="src/example.py", content=WF_CONTENT_SHORT)
        await self._switch_to_approval_app("write_file", args)


def test_snapshot_write_approval_long_content_bottom_lines_hidden(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)
        approval = pilot.app.query_one(ApprovalApp)
        scroll = approval.query_one(".approval-tool-info-scroll", VerticalScroll)
        scroll.scroll_end(animate=False, immediate=True)
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_diff_view_truncation.py:WriteApprovalLongContentApp",
        terminal_size=(100, 30),
        run_before=run_before,
    )


def test_snapshot_write_approval_short_content(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_diff_view_truncation.py:WriteApprovalShortContentApp",
        terminal_size=(100, 30),
        run_before=run_before,
    )


def test_snapshot_write_approval_long_content_after_resize(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)
        await pilot.resize_terminal(120, 40)
        await pilot.pause(0.2)
        approval = pilot.app.query_one(ApprovalApp)
        scroll = approval.query_one(".approval-tool-info-scroll", VerticalScroll)
        scroll.scroll_end(animate=False, immediate=True)
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_diff_view_truncation.py:WriteApprovalLongContentApp",
        terminal_size=(100, 30),
        run_before=run_before,
    )
