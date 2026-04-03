"""Snapshot tests for empty assistant message removed when reasoning starts (e.g. Opus)."""

from __future__ import annotations

from textual.pilot import Pilot

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_backend import FakeBackend


class SnapshotTestAppEmptyAssistantThenReasoning(BaseSnapshotTestApp):
    """Backend stream: first chunk is assistant content only (empty), then reasoning.

    Ensures the empty assistant bubble is removed when the first reasoning chunk
    arrives, so the UI does not show a blank assistant message above the thinking block.
    """

    def __init__(self) -> None:
        config = default_config()
        fake_backend = FakeBackend(
            chunks=[
                mock_llm_chunk(content="\n\n"),
                mock_llm_chunk(
                    content="", reasoning_content="Let me think about this..."
                ),
                mock_llm_chunk(
                    content="", reasoning_content=" Considering the options."
                ),
                mock_llm_chunk(content="The answer is 42."),
            ]
        )
        super().__init__(config=config)
        self.agent_loop = build_test_agent_loop(
            config=config,
            agent_name=self._current_agent_name,
            enable_streaming=True,
            backend=fake_backend,
        )


def test_snapshot_empty_assistant_removed_when_reasoning_starts(
    snap_compare: SnapCompare,
) -> None:
    """Empty assistant message is removed when reasoning starts; no blank bubble above thinking."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"What is the answer?")
        await pilot.press("enter")
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_empty_assistant_before_reasoning.py:SnapshotTestAppEmptyAssistantThenReasoning",
        terminal_size=(120, 36),
        run_before=run_before,
    )
