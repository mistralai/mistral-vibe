from __future__ import annotations

import asyncio
from typing import Any

import pytest

from vibe.cli.turn_summary import TurnSummaryResult, TurnSummaryTracker


class FakeSummaryGenerator:
    def __init__(
        self, summary: str | None = "summary", *, error: Exception | None = None
    ) -> None:
        self.summary = summary
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.gate: asyncio.Event | None = None

    async def summarize(self, **kwargs: Any) -> str | None:
        self.calls.append(kwargs)
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.summary


def _tracker(generator: FakeSummaryGenerator | None = None) -> TurnSummaryTracker:
    return TurnSummaryTracker(generator=generator or FakeSummaryGenerator())


def test_tracks_turn_input() -> None:
    tracker = _tracker()

    tracker.start_turn("request")
    tracker.track_user_message("message-1")
    tracker.track_assistant_text("part one")
    tracker.track_assistant_text("")
    tracker.track_assistant_text(" part two")
    tracker.set_error("warning")

    assert tracker._data is not None
    assert tracker._data.user_message == "request"
    assert tracker._data.message_id == "message-1"
    assert tracker._data.assistant_fragments == ["part one", " part two"]
    assert tracker._data.error == "warning"


def test_start_turn_increments_generation() -> None:
    tracker = _tracker()

    tracker.start_turn("one")
    tracker.start_turn("two")

    assert tracker.generation == 2


def test_cancel_turn_discards_pending_input() -> None:
    tracker = _tracker()
    tracker.start_turn("request")

    tracker.cancel_turn()

    assert tracker._data is None
    assert tracker.end_turn() is None


@pytest.mark.asyncio
async def test_end_turn_sends_public_summary_input() -> None:
    generator = FakeSummaryGenerator("result")
    tracker = _tracker(generator)
    results: list[TurnSummaryResult] = []
    tracker.on_summary = results.append
    tracker.start_turn("request")
    tracker.track_user_message("message-1")
    tracker.track_assistant_text("part one")
    tracker.track_assistant_text(" part two")
    tracker.set_error("warning")

    tracker.end_turn()
    await asyncio.sleep(0)

    assert generator.calls == [
        {
            "user_message": "request",
            "assistant_text": "part one part two",
            "error": "warning",
            "message_id": "message-1",
        }
    ]
    assert results == [TurnSummaryResult(generation=1, summary="result")]
    assert tracker._data is None


@pytest.mark.asyncio
async def test_request_failure_reports_empty_summary() -> None:
    generator = FakeSummaryGenerator(error=RuntimeError("failed"))
    tracker = _tracker(generator)
    results: list[TurnSummaryResult] = []
    tracker.on_summary = results.append
    tracker.start_turn("request")

    tracker.end_turn()
    await asyncio.sleep(0)

    assert results == [TurnSummaryResult(generation=1, summary=None)]


@pytest.mark.asyncio
async def test_close_cancels_in_flight_summary() -> None:
    generator = FakeSummaryGenerator()
    generator.gate = asyncio.Event()
    tracker = _tracker(generator)
    tracker.start_turn("request")
    tracker.end_turn()
    await asyncio.sleep(0)

    await tracker.close()

    assert not tracker._tasks
