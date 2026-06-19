from __future__ import annotations

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from vibe.cli.textual_ui.widgets.session_picker import SessionPickerApp
from vibe.core.session.resume_sessions import ResumeSessionInfo

_SESSIONS = [
    ResumeSessionInfo(
        session_id="local-session-0001",
        cwd="/test/workdir",
        title="Refactor the auth module",
        end_time=None,
    ),
    ResumeSessionInfo(
        session_id="local-session-0002",
        cwd="/test/workdir",
        title="Add unit tests for the API",
        end_time=None,
    ),
]

_LATEST_MESSAGES = {
    _SESSIONS[0].option_id: "Refactor the auth module",
    _SESSIONS[1].option_id: "Add unit tests for the API",
}


class SessionPickerTestApp(BaseSnapshotTestApp):
    async def on_mount(self) -> None:
        await super().on_mount()
        picker = SessionPickerApp(
            sessions=_SESSIONS, latest_messages=_LATEST_MESSAGES, cwd="/test/workdir"
        )
        await self._switch_from_input(picker)


def test_snapshot_session_picker_header(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_session_picker.py:SessionPickerTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )
