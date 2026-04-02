from __future__ import annotations

from unittest.mock import patch

import pytest
from textual.pilot import Pilot

from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_backend import FakeBackend
from vibe.core.rewind import RewindError


class RewindSnapshotApp(BaseSnapshotTestApp):
    """Test app with a multi-turn conversation for rewind snapshots."""

    def __init__(self) -> None:
        fake_backend = FakeBackend([
            mock_llm_chunk(content="Hello! How can I help you?")
        ])
        super().__init__(backend=fake_backend)


async def _send_messages(pilot: Pilot) -> None:
    """Send three messages to build up conversation history.

    Also patches ``has_file_changes_at`` to always return True so the
    rewind panel shows the "restore files" option.
    """
    for msg in ["first message", "second message", "third message"]:
        await pilot.press(*msg)
        await pilot.press("enter")
        await pilot.pause(0.4)

    app: RewindSnapshotApp = pilot.app  # type: ignore[assignment]
    rm = app.agent_loop.rewind_manager
    patch.object(rm, "has_file_changes_at", return_value=True).start()


def test_snapshot_rewind_panel_shown(snap_compare: SnapCompare) -> None:
    """Pressing alt+up enters rewind mode and shows the panel."""

    async def run_before(pilot: Pilot) -> None:
        await _send_messages(pilot)
        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_rewind.py:RewindSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_rewind_navigate_up(snap_compare: SnapCompare) -> None:
    """Pressing alt+up twice selects the second-to-last message."""

    async def run_before(pilot: Pilot) -> None:
        await _send_messages(pilot)
        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.2)
        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_rewind.py:RewindSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_rewind_navigate_down(snap_compare: SnapCompare) -> None:
    """Navigate up twice then down once returns to the last message."""

    async def run_before(pilot: Pilot) -> None:
        await _send_messages(pilot)
        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.2)
        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.2)
        await pilot.press("alt+down")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_rewind.py:RewindSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_rewind_exit_on_escape(snap_compare: SnapCompare) -> None:
    """Pressing escape exits rewind mode and restores the input panel."""

    async def run_before(pilot: Pilot) -> None:
        await _send_messages(pilot)
        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_rewind.py:RewindSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_rewind_error_shows_toast(
    snap_compare: SnapCompare, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When rewind_to_message fails, a toast is shown and rewind mode stays active."""

    async def failing_rewind(*_args, **_kwargs):
        raise RewindError("Invalid message index: 99")

    async def run_before(pilot: Pilot) -> None:
        await _send_messages(pilot)
        app: RewindSnapshotApp = pilot.app  # type: ignore[assignment]
        monkeypatch.setattr(
            app.agent_loop.rewind_manager, "rewind_to_message", failing_rewind
        )
        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.2)
        await pilot.press("enter")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_rewind.py:RewindSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
