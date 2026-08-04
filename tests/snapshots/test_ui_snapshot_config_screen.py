from __future__ import annotations

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare


class ConfigScreenTestApp(BaseSnapshotTestApp):
    async def on_mount(self) -> None:
        await super().on_mount()
        await self._show_config()


def test_snapshot_config_screen_initial(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_config_screen.py:ConfigScreenTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )


def test_snapshot_config_screen_edit_modal(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)
        for char in "theme":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_config_screen.py:ConfigScreenTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )


def test_snapshot_config_screen_search(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)
        for char in "enable":
            await pilot.press(char)
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_config_screen.py:ConfigScreenTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )
