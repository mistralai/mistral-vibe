from __future__ import annotations

from enum import StrEnum, auto
from typing import Protocol

from vibe.core.watchdog.models import RunPhase


class DeadlineStatus(StrEnum):
    ACTIVE = auto()
    WAITING = auto()
    SUSPECTED = auto()
    HEALTHY = auto()
    CONFIRMED = auto()


class LivenessProbe(Protocol):
    async def responsive(self, phase: RunPhase) -> bool: ...


_WAIT_PHASES = {RunPhase.WAITING_FOR_APPROVAL, RunPhase.WAITING_FOR_USER}


class PhaseDeadlineTracker:
    def __init__(self, timeouts: dict[RunPhase, float]) -> None:
        if any(timeout <= 0 for timeout in timeouts.values()):
            raise ValueError("phase deadlines must be positive")
        self._timeouts = timeouts
        self._phase = RunPhase.IDLE
        self._deadline: float | None = None

    def activity(self, phase: RunPhase, *, now: float) -> DeadlineStatus:
        self._phase = phase
        if phase in _WAIT_PHASES or phase == RunPhase.IDLE:
            self._deadline = None
            return DeadlineStatus.WAITING
        timeout = self._timeouts.get(phase)
        self._deadline = now + timeout if timeout is not None else None
        return DeadlineStatus.ACTIVE

    async def evaluate(self, *, now: float, probe: LivenessProbe) -> DeadlineStatus:
        if self._deadline is None:
            return DeadlineStatus.WAITING
        if now < self._deadline:
            return DeadlineStatus.ACTIVE
        if await probe.responsive(self._phase):
            self._deadline = now + self._timeouts[self._phase]
            return DeadlineStatus.HEALTHY
        return DeadlineStatus.CONFIRMED
