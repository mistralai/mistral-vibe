from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

from vibe.core.watchdog.detectors.base import DetectorObservation, DetectorVerdict
from vibe.core.watchdog.events import EventKind, WatchdogEvent
from vibe.core.watchdog.fingerprint import (
    fingerprint,
    fingerprint_call,
    fingerprint_result,
)
from vibe.core.watchdog.models import Evidence, RunState


@dataclass(frozen=True, slots=True)
class _Completion:
    result: str
    repository: str


_MIN_REPEAT_THRESHOLD = 2


class RepeatedCallDetector:
    detector_id = "repeated_call"

    def __init__(self, *, threshold: int = 2, evidence_window: int = 20) -> None:
        if threshold < _MIN_REPEAT_THRESHOLD:
            raise ValueError("repeated-call threshold must be at least 2")
        self._threshold = threshold
        self._evidence_window = evidence_window
        self._calls: dict[str, str] = {}
        self._counts: dict[str, int] = {}
        self._completions: dict[str, _Completion] = {}
        self._suspected: set[str] = set()

    def reset(self) -> None:
        self._calls.clear()
        self._counts.clear()
        self._completions.clear()
        self._suspected.clear()

    def observe(
        self, event: WatchdogEvent, state: RunState
    ) -> list[DetectorObservation]:
        del state
        if event.kind == EventKind.TOOL_STARTED:
            return self._observe_call(event)
        if event.kind == EventKind.TOOL_FINISHED:
            return self._observe_result(event)
        return []

    def _observe_call(self, event: WatchdogEvent) -> list[DetectorObservation]:
        call_id = _string(event.payload, "tool_call_id")
        tool_name = _string(event.payload, "tool_name")
        arguments = event.payload.get("arguments")
        if call_id is None or tool_name is None or not isinstance(arguments, dict):
            return []
        call_fingerprint = fingerprint_call(tool_name, arguments)
        self._calls[call_id] = call_fingerprint
        count = self._counts.get(call_fingerprint, 0) + 1
        self._counts[call_fingerprint] = count
        if count < self._threshold:
            return []
        self._suspected.add(call_id)
        return [
            self._observation(
                event,
                DetectorVerdict.SUSPECTED,
                call_fingerprint,
                "repeated_call_threshold",
                {"tool_name": tool_name, "repeat_count": count},
            )
        ]

    def _observe_result(self, event: WatchdogEvent) -> list[DetectorObservation]:
        call_id = _string(event.payload, "tool_call_id")
        repository = _string(event.payload, "repository_fingerprint")
        if call_id is None or repository is None:
            return []
        call_fingerprint = self._calls.pop(call_id, None)
        if call_fingerprint is None:
            return []
        result_fingerprint = fingerprint_result(
            "error" if event.payload.get("error") else "result",
            event.payload.get("error") or event.payload.get("result"),
        )
        completion = _Completion(result=result_fingerprint, repository=repository)
        previous = self._completions.get(call_fingerprint)
        self._completions[call_fingerprint] = completion
        if call_id not in self._suspected:
            return []
        self._suspected.discard(call_id)
        facts: dict[str, JsonValue] = {
            "call": call_fingerprint,
            "result": result_fingerprint,
            "repository": repository,
        }
        if previous == completion:
            return [
                self._observation(
                    event,
                    DetectorVerdict.CONFIRMED,
                    fingerprint(facts),
                    "same_result_and_repository",
                    facts,
                )
            ]
        return [
            self._observation(
                event,
                DetectorVerdict.HEALTHY,
                fingerprint(facts),
                "result_or_repository_changed",
                facts,
            )
        ]

    def _observation(
        self,
        event: WatchdogEvent,
        verdict: DetectorVerdict,
        evidence_fingerprint: str,
        reason: str,
        facts: dict[str, JsonValue],
    ) -> DetectorObservation:
        return DetectorObservation(
            detector_id=self.detector_id,
            verdict=verdict,
            reason=reason,
            evidence=Evidence(
                detector=self.detector_id,
                fingerprint=evidence_fingerprint,
                observed_sequence=event.sequence,
                expires_at_sequence=event.sequence + self._evidence_window,
                facts=facts,
            ),
        )


def _string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None
