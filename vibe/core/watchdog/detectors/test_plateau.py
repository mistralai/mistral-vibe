from __future__ import annotations

from vibe.core.watchdog.detectors.base import DetectorObservation
from vibe.core.watchdog.events import WatchdogEvent
from vibe.core.watchdog.models import RunState


class TestPlateauDetector:
    detector_id = "test_plateau"

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled

    def reset(self) -> None:
        pass

    def observe(
        self, event: WatchdogEvent, state: RunState
    ) -> list[DetectorObservation]:
        del event, state
        return []
