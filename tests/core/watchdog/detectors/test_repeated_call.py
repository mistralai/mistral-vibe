from __future__ import annotations

from vibe.core.watchdog.detectors import DetectorVerdict, RepeatedCallDetector
from vibe.core.watchdog.events import EventKind, WatchdogEvent
from vibe.core.watchdog.models import RunState


def event(sequence: int, kind: EventKind, payload: dict) -> WatchdogEvent:
    return WatchdogEvent(
        run_id="run-1",
        session_id="session-1",
        sequence=sequence,
        observed_at_monotonic=float(sequence),
        kind=kind,
        payload=payload,
    )


def test_same_call_result_and_repository_confirms() -> None:
    detector = RepeatedCallDetector(threshold=2)
    state = RunState.new(run_id="run-1", session_id="session-1")

    assert (
        detector.observe(
            event(
                1,
                EventKind.TOOL_STARTED,
                {"tool_call_id": "a", "tool_name": "bash", "arguments": {"cmd": "x"}},
            ),
            state,
        )
        == []
    )
    assert (
        detector.observe(
            event(
                2,
                EventKind.TOOL_FINISHED,
                {
                    "tool_call_id": "a",
                    "result": "failed",
                    "repository_fingerprint": "repo-1",
                },
            ),
            state,
        )
        == []
    )
    suspected = detector.observe(
        event(
            3,
            EventKind.TOOL_STARTED,
            {"tool_call_id": "b", "tool_name": "bash", "arguments": {"cmd": "x"}},
        ),
        state,
    )
    confirmed = detector.observe(
        event(
            4,
            EventKind.TOOL_FINISHED,
            {
                "tool_call_id": "b",
                "result": "failed",
                "repository_fingerprint": "repo-1",
            },
        ),
        state,
    )

    assert suspected[0].verdict == DetectorVerdict.SUSPECTED
    assert confirmed[0].verdict == DetectorVerdict.CONFIRMED


def test_default_threshold_requires_four_exact_calls() -> None:
    detector = RepeatedCallDetector()
    state = RunState.new(run_id="run-1", session_id="session-1")
    observations = []

    for attempt in range(1, 5):
        observations.extend(
            detector.observe(
                event(
                    attempt * 2 - 1,
                    EventKind.TOOL_STARTED,
                    {
                        "tool_call_id": str(attempt),
                        "tool_name": "bash",
                        "arguments": {"cmd": "x"},
                    },
                ),
                state,
            )
        )
        observations.extend(
            detector.observe(
                event(
                    attempt * 2,
                    EventKind.TOOL_FINISHED,
                    {
                        "tool_call_id": str(attempt),
                        "result": "failed",
                        "repository_fingerprint": "repo-1",
                    },
                ),
                state,
            )
        )
        if attempt < 4:
            assert observations == []

    assert [item.verdict for item in observations] == [
        DetectorVerdict.SUSPECTED,
        DetectorVerdict.CONFIRMED,
    ]


def test_repository_change_resets_suspicion() -> None:
    detector = RepeatedCallDetector(threshold=2)
    state = RunState.new(run_id="run-1", session_id="session-1")
    for item in [
        event(
            1,
            EventKind.TOOL_STARTED,
            {"tool_call_id": "a", "tool_name": "bash", "arguments": {"cmd": "x"}},
        ),
        event(
            2,
            EventKind.TOOL_FINISHED,
            {
                "tool_call_id": "a",
                "result": "failed",
                "repository_fingerprint": "repo-1",
            },
        ),
        event(
            3,
            EventKind.TOOL_STARTED,
            {"tool_call_id": "b", "tool_name": "bash", "arguments": {"cmd": "x"}},
        ),
    ]:
        detector.observe(item, state)

    observations = detector.observe(
        event(
            4,
            EventKind.TOOL_FINISHED,
            {
                "tool_call_id": "b",
                "result": "failed",
                "repository_fingerprint": "repo-2",
            },
        ),
        state,
    )

    assert observations[0].verdict == DetectorVerdict.HEALTHY


def test_missing_repository_evidence_cannot_confirm() -> None:
    detector = RepeatedCallDetector(threshold=2)
    state = RunState.new(run_id="run-1", session_id="session-1")
    detector.observe(
        event(
            1,
            EventKind.TOOL_STARTED,
            {"tool_call_id": "a", "tool_name": "bash", "arguments": {"cmd": "x"}},
        ),
        state,
    )

    assert (
        detector.observe(
            event(
                2, EventKind.TOOL_FINISHED, {"tool_call_id": "a", "result": "failed"}
            ),
            state,
        )
        == []
    )
