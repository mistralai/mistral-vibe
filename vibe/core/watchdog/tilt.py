from __future__ import annotations

from vibe.core.watchdog.events import EventKind, WatchdogEvent


class TiltTracker:
    def __init__(self, *, fresh_window: int = 3) -> None:
        if fresh_window < 1:
            raise ValueError("fresh TILT window must be positive")
        self._fresh_window = fresh_window
        self._tilted = False
        self._last_sequence: int | None = None
        self._fresh_count = 0

    def enter(self) -> None:
        self._tilted = True
        self._last_sequence = None
        self._fresh_count = 0

    def observe(self, event: WatchdogEvent) -> EventKind | None:
        if not self._tilted:
            return None
        if (
            self._last_sequence is not None
            and event.sequence != self._last_sequence + 1
        ):
            self._fresh_count = 1
        else:
            self._fresh_count += 1
        self._last_sequence = event.sequence
        if self._fresh_count < self._fresh_window:
            return None
        self._tilted = False
        return EventKind.TILT_CLEARED
