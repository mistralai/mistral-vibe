from __future__ import annotations

from textual.pilot import Pilot
from textual.widgets import Static

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare


class ClipboardNoticeSnapshotApp(BaseSnapshotTestApp):
    pass


def test_snapshot_clipboard_notice_visible(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        notice = pilot.app.query_one("#clipboard-notice", Static)
        notice.update("Selection copied to clipboard")
        notice.display = True
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_clipboard_notice.py:ClipboardNoticeSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
