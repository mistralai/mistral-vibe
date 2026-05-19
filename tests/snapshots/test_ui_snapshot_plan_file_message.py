from __future__ import annotations

from pathlib import Path
from typing import cast

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from vibe.cli.textual_ui.widgets.messages import PlanFileMessage

PLAN_CONTENT = """\
# Implementation Plan

## 1. Add user authentication
- JWT token validation
- Session management

## 2. Database migrations
- Create users table
- Add indexes
"""


class PlanFileMessageTestApp(BaseSnapshotTestApp):
    pass


def test_snapshot_plan_file_message(snap_compare: SnapCompare, tmp_path: Path) -> None:
    plan_file = tmp_path / "plan.md"
    plan_file.write_text(PLAN_CONTENT)

    async def run_before(pilot: Pilot) -> None:
        app = cast(PlanFileMessageTestApp, pilot.app)
        plan_widget = PlanFileMessage(file_path=plan_file)
        await app._mount_and_scroll(plan_widget)
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_plan_file_message.py:PlanFileMessageTestApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
