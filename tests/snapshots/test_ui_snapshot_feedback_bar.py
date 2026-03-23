from __future__ import annotations

from unittest.mock import patch

import pytest
from textual.pilot import Pilot

from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_backend import FakeBackend


@pytest.fixture(autouse=True)
def _enable_feedback_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.widgets.feedback_bar.FEEDBACK_PROBABILITY", 1
    )


class FeedbackBarSnapshotApp(BaseSnapshotTestApp):
    def __init__(self) -> None:
        fake_backend = FakeBackend(
            mock_llm_chunk(
                content="Sure, I can help with that.",
                prompt_tokens=10_000,
                completion_tokens=2_500,
            )
        )
        super().__init__(backend=fake_backend)


def test_snapshot_feedback_bar_visible(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        with patch(
            "vibe.cli.textual_ui.widgets.feedback_bar.random.random", return_value=0
        ):
            await pilot.press(*"Hello")
            await pilot.press("enter")
            await pilot.pause(0.4)

    assert snap_compare(
        "test_ui_snapshot_feedback_bar.py:FeedbackBarSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
