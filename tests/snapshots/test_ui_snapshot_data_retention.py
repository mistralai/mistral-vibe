from __future__ import annotations

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare


class DataRetentionTestApp(BaseSnapshotTestApp):
    async def on_mount(self) -> None:
        await super().on_mount()
        await self._show_data_retention()


def test_snapshot_data_retention(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_data_retention.py:DataRetentionTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )
