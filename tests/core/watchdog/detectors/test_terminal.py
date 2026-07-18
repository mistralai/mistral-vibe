from __future__ import annotations

from vibe.core.watchdog.detectors import DetectorVerdict, TerminalDetector
from vibe.core.watchdog.events import EventKind, WatchdogEvent
from vibe.core.watchdog.models import RunState


def test_typed_terminal_failure_confirms_immediately() -> None:
    detector = TerminalDetector()
    state = RunState.new(run_id="run-1", session_id="session-1")
    event = WatchdogEvent(
        run_id="run-1",
        session_id="session-1",
        sequence=1,
        observed_at_monotonic=1,
        kind=EventKind.RUN_FAILED,
        payload={"error_class": "ConversationLimitException"},
    )

    observations = detector.observe(event, state)

    assert observations[0].verdict == DetectorVerdict.CONFIRMED
