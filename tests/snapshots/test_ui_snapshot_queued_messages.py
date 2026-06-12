from __future__ import annotations

from typing import cast

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer


class QueuedMessagesSnapshotApp(BaseSnapshotTestApp):
    pass


async def _enqueue_while_busy(pilot: Pilot, submissions: list[str]) -> None:
    app = cast(VibeApp, pilot.app)
    chat_input = app.query_one(ChatInputContainer)
    # leave _agent_running set so the queue drain stays blocked for the snapshot
    app._agent_running = True
    for value in submissions:
        chat_input.post_message(ChatInputContainer.Submitted(value))
        await pilot.pause(0.1)


def test_snapshot_queued_user_prompts(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _enqueue_while_busy(
            pilot, ["first follow-up", "second follow-up", "third follow-up"]
        )
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_queued_messages.py:QueuedMessagesSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_queued_bash_commands(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _enqueue_while_busy(pilot, ["!echo first", "!echo second", "!ls /tmp"])
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_queued_messages.py:QueuedMessagesSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_queued_mixed_prompts_and_bash(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _enqueue_while_busy(
            pilot,
            [
                "please refactor this",
                "!pytest -x",
                "then commit the change",
                "!git status",
            ],
        )
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_queued_messages.py:QueuedMessagesSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
