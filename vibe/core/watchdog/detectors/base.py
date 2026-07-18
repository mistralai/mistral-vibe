from __future__ import annotations

from enum import StrEnum, auto
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from vibe.core.watchdog.events import WatchdogEvent
from vibe.core.watchdog.models import Evidence, RunState


class DetectorVerdict(StrEnum):
    HEALTHY = auto()
    SUSPECTED = auto()
    CONFIRMED = auto()


class DetectorObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    detector_id: str
    verdict: DetectorVerdict
    evidence: Evidence
    reason: str


class Detector(Protocol):
    detector_id: str

    def reset(self) -> None: ...

    def observe(
        self, event: WatchdogEvent, state: RunState
    ) -> list[DetectorObservation]: ...
