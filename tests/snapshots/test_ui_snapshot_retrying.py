from __future__ import annotations

import asyncio
from typing import cast

import pytest
from textual.pilot import Pilot

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_backend import FakeBackend


class GatedRetryBackend(FakeBackend):
    """Reports a retry, then parks so the status can be captured.

    A real backend is inside its own retry backoff at this point. Without the
    gate the response lands immediately and the next delta resets the label
    before the screenshot is taken.
    """

    def __init__(self) -> None:
        super().__init__(
            mock_llm_chunk(content="Recovered."), retries_before_response=1
        )
        self.parked = asyncio.Event()

    async def notice_retries(self) -> None:
        await super().notice_retries()
        self.parked.set()
        await asyncio.Event().wait()


class SnapshotTestAppRetrying(BaseSnapshotTestApp):
    def __init__(self) -> None:
        self.retry_backend = GatedRetryBackend()
        self.loop_under_test = build_test_agent_loop(
            config=default_config(), backend=self.retry_backend
        )
        super().__init__(agent_loop=self.loop_under_test)


def test_snapshot_shows_retrying_status(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        app = cast(SnapshotTestAppRetrying, pilot.app)
        app.retry_backend.on_retry = app.loop_under_test.notice_retry

        await pilot.press(*"Hello")
        await pilot.press("enter")
        await asyncio.wait_for(app.retry_backend.parked.wait(), timeout=5)
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_retrying.py:SnapshotTestAppRetrying",
        terminal_size=(120, 24),
        run_before=run_before,
    )


def test_easter_eggs_replace_only_the_model_is_working_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A joke may stand in for "Generating"/"Thinking", never for a factual status."""
    from vibe.cli.textual_ui.widgets import loading

    # Force the easter egg to fire on every status change.
    monkeypatch.setattr(loading.random, "random", lambda: 0.0)
    eggs = loading.LoadingWidget.EASTER_EGGS
    widget = loading.LoadingWidget()
    assert widget.status in eggs

    for status in (loading.RETRYING_LOADING_STATUS, "Reading main.py", "Running hook"):
        widget.set_status(status)
        assert widget.status == status

    for status in (loading.DEFAULT_LOADING_STATUS, loading.THINKING_LOADING_STATUS):
        widget.set_status(status)
        assert widget.status in eggs
