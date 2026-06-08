from __future__ import annotations

from pathlib import Path

from textual.pilot import Pilot

from tests.snapshots.snap_compare import SnapCompare
from vibe.setup.update_prompt import update_prompt_dialog
from vibe.setup.update_prompt.update_prompt_dialog import UpdatePromptApp


class UpdatePromptSnapshotApp(UpdatePromptApp):
    CSS_PATH = str(
        Path(update_prompt_dialog.__file__).parent / "update_prompt_dialog.tcss"
    )

    def __init__(self) -> None:
        super().__init__(current_version="1.0.0", latest_version="2.0.0")


def test_snapshot_update_prompt_initial(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_update_prompt.py:UpdatePromptSnapshotApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_update_prompt_continue_selected(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)
        await pilot.press("right")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_update_prompt.py:UpdatePromptSnapshotApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )
