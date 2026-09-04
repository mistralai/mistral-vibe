from __future__ import annotations

import asyncio
from typing import cast

from textual.pilot import Pilot

from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer


class _BlockingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__(mock_llm_chunk(content="done"))
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, **kwargs):
        self.started.set()
        await self.release.wait()
        return await super().complete(**kwargs)


class QueuedMessagesSnapshotApp(BaseSnapshotTestApp):
    def __init__(self) -> None:
        self.blocking_backend = _BlockingBackend()
        super().__init__(backend=self.blocking_backend)


async def _enqueue_while_busy(pilot: Pilot, submissions: list[str]) -> None:
    app = cast(QueuedMessagesSnapshotApp, pilot.app)
    chat_input = app.query_one(ChatInputContainer)
    app._agent_task = asyncio.create_task(app._handle_turn("snapshot blocker"))
    await app.blocking_backend.started.wait()
    await app._remove_loading_widget()
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
