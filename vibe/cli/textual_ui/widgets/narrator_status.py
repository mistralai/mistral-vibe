from __future__ import annotations

from enum import StrEnum, auto
from typing import Any

from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Static

SHRINK_FRAMES = "█▇▆▅▄▃▂▁"
BAR_FRAMES = ["▂▅▇", "▃▆▅", "▅▃▇", "▇▂▅", "▅▇▃", "▃▅▆"]
ANIMATION_INTERVAL = 0.15


class NarratorState(StrEnum):
    IDLE = auto()
    SUMMARIZING = auto()
    SPEAKING = auto()


class NarratorStatus(Static):
    state = reactive(NarratorState.IDLE)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._timer: Timer | None = None
        self._frame: int = 0

    def watch_state(self, new_state: NarratorState) -> None:
        self._stop_timer()
        match new_state:
            case NarratorState.IDLE:
                self.update("")
            case NarratorState.SUMMARIZING | NarratorState.SPEAKING:
                self._frame = 0
                self._tick()
                self._timer = self.set_interval(ANIMATION_INTERVAL, self._tick)

    def _tick(self) -> None:
        match self.state:
            case NarratorState.SUMMARIZING:
                char = SHRINK_FRAMES[self._frame % len(SHRINK_FRAMES)]
                self.update(
                    f"[bold orange]{char}[/bold orange] summarizing [dim]esc to stop[/dim]"
                )
            case NarratorState.SPEAKING:
                bars = BAR_FRAMES[self._frame % len(BAR_FRAMES)]
                self.update(
                    f"[bold orange]{bars}[/bold orange] speaking [dim]esc to stop[/dim]"
                )
        self._frame += 1

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
