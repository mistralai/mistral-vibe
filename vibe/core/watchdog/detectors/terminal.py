from __future__ import annotations

from vibe.core.watchdog.detectors.base import DetectorObservation, DetectorVerdict
from vibe.core.watchdog.events import EventKind, WatchdogEvent
from vibe.core.watchdog.fingerprint import fingerprint_error
from vibe.core.watchdog.models import Evidence, RunState


class TerminalDetector:
    detector_id = "typed_terminal"

    def reset(self) -> None:
        pass

    def observe(
        self, event: WatchdogEvent, state: RunState
    ) -> list[DetectorObservation]:
        del state
        if event.kind != EventKind.RUN_FAILED:
            return []
        error_class = event.payload.get("error_class")
        if not isinstance(error_class, str):
            return []
        return [
            DetectorObservation(
                detector_id=self.detector_id,
                verdict=DetectorVerdict.CONFIRMED,
                reason="typed_terminal_failure",
                evidence=Evidence(
                    detector=self.detector_id,
                    fingerprint=fingerprint_error(error_class, "terminal"),
                    observed_sequence=event.sequence,
                    facts={"error_class": error_class},
                ),
            )
        ]
