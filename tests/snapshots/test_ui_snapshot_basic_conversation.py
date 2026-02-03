from __future__ import annotations

from textual.pilot import Pilot

from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_backend import FakeBackend


class SnapshotTestAppWithConversation(BaseSnapshotTestApp):
    def __init__(self) -> None:
        fake_backend = FakeBackend(
            mock_llm_chunk(
                content="I'm the Vibe agent and I'm ready to help.",
                prompt_tokens=10_000,
                completion_tokens=2_500,
            )
        )
        super().__init__(backend=fake_backend)


def test_snapshot_shows_basic_conversation(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"Hello there, who are you?")
        await pilot.press("enter")
        await pilot.pause(0.4)

    assert snap_compare(
        "test_ui_snapshot_basic_conversation.py:SnapshotTestAppWithConversation",
        terminal_size=(120, 36),
        run_before=run_before,
    )
