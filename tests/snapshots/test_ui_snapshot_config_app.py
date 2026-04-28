from __future__ import annotations

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare


class ConfigTestApp(BaseSnapshotTestApp):
    async def on_mount(self) -> None:
        await super().on_mount()
        await self._switch_to_config_app()


def test_snapshot_config_initial(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_config_app.py:ConfigTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )


def test_snapshot_config_navigate_down(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)
        await pilot.press("down")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_config_app.py:ConfigTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )


def test_snapshot_config_toggle_autocopy(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_config_app.py:ConfigTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )


def test_snapshot_config_escape_closes(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_config_app.py:ConfigTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )
