from __future__ import annotations

from typing import cast

import pytest
from textual.pilot import Pilot

from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)


class RewindSnapshotApp(BaseSnapshotTestApp):
    """Test app with a multi-turn conversation for rewind snapshots."""

    def __init__(self) -> None:
        fake_backend = FakeBackend([
            mock_llm_chunk(content="Hello! How can I help you?")
        ])
        super().__init__(backend=fake_backend)


async def _send_messages(pilot: Pilot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Send three messages to build up conversation history.

    The public rewind preflight returns true so the panel shows the
    "restore files" option.
    """
    for msg in ["first message", "second message", "third message"]:
        await pilot.press(*msg)
        await pilot.press("enter")
        await pilot.pause(0.4)

    app = cast(RewindSnapshotApp, pilot.app)

    async def has_file_changes(_entry_id: str) -> bool:
        return True

    monkeypatch.setattr(
        app.app_server.resources.sessions, "rewind_has_file_changes", has_file_changes
    )


async def _enter_rewind(pilot: Pilot) -> None:
    await pilot.press("escape", "escape")
    await pilot.pause(0.2)


def test_snapshot_rewind_panel_shown(
    snap_compare: SnapCompare, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Double-Esc enters rewind mode and shows the panel."""

    async def run_before(pilot: Pilot) -> None:
        await _send_messages(pilot, monkeypatch)
        await _enter_rewind(pilot)

    assert snap_compare(
        "test_ui_snapshot_rewind.py:RewindSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_rewind_navigate_up(
    snap_compare: SnapCompare, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Going previous selects the second-to-last message."""

    async def run_before(pilot: Pilot) -> None:
        await _send_messages(pilot, monkeypatch)
        await _enter_rewind(pilot)
        await pilot.press("left")
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_rewind.py:RewindSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_rewind_navigate_down(
    snap_compare: SnapCompare, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Navigate previous then next returns to the last message."""

    async def run_before(pilot: Pilot) -> None:
        await _send_messages(pilot, monkeypatch)
        await _enter_rewind(pilot)
        await pilot.press("left")
        await pilot.pause(0.2)
        await pilot.press("right")
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_rewind.py:RewindSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_rewind_exit_on_quit(
    snap_compare: SnapCompare, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pressing q exits rewind mode and restores the input panel."""

    async def run_before(pilot: Pilot) -> None:
        await _send_messages(pilot, monkeypatch)
        await _enter_rewind(pilot)
        await pilot.press("q")
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_rewind.py:RewindSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_rewind_error_shows_toast(
    snap_compare: SnapCompare, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed rewind request shows a toast and leaves rewind mode active."""

    async def failing_rewind(*_args, **_kwargs):
        raise AppServerResponseError(
            ProtocolError(
                code=ProtocolErrorCode.INTERNAL_ERROR,
                message="Invalid message index: 99",
            )
        )

    async def run_before(pilot: Pilot) -> None:
        await _send_messages(pilot, monkeypatch)
        app = cast(RewindSnapshotApp, pilot.app)
        monkeypatch.setattr(app.app_server.resources.sessions, "rewind", failing_rewind)
        await _enter_rewind(pilot)
        await pilot.press("enter")
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_rewind.py:RewindSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
