from __future__ import annotations

from enum import StrEnum, auto


class ExitClassification(StrEnum):
    RUNNING = auto()
    UNEXPECTED = auto()
    USER_REQUESTED = auto()
    TERMINAL_LIMIT = auto()


class SentinelAction(StrEnum):
    MONITOR = auto()
    TILT_GRACE = auto()
    RESTART = auto()
    NEEDS_USER = auto()


class SentinelPolicy:
    def __init__(self, *, restart_cap: int = 2) -> None:
        if restart_cap < 0:
            raise ValueError("restart cap cannot be negative")
        self._restart_cap = restart_cap

    def decide(
        self,
        *,
        exit_classification: ExitClassification,
        heartbeat_stale: bool,
        clock_discontinuity: bool,
        process_responsive: bool,
        grace_elapsed: bool,
        restart_count: int,
    ) -> SentinelAction:
        if exit_classification in {
            ExitClassification.USER_REQUESTED,
            ExitClassification.TERMINAL_LIMIT,
        }:
            return SentinelAction.MONITOR
        restart_needed = exit_classification == ExitClassification.UNEXPECTED or (
            heartbeat_stale
            and not process_responsive
            and grace_elapsed
            and not clock_discontinuity
        )
        if restart_needed:
            if restart_count >= self._restart_cap:
                return SentinelAction.NEEDS_USER
            return SentinelAction.RESTART
        if clock_discontinuity or (heartbeat_stale and not grace_elapsed):
            return SentinelAction.TILT_GRACE
        return SentinelAction.MONITOR
