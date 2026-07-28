from __future__ import annotations

from typing import Any

from textual.content import Content
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Static

from vibe.cli.narrator_manager.narrator_manager_port import (
    NarratorManagerListener,
    NarratorManagerPort,
    NarratorState,
)
from vibe.cli.textual_ui.shortcut_hints import shortcut

SHRINK_FRAMES = "█▇▆▅▄▃▂▁"
BAR_FRAMES = ["▂▅▇", "▃▆▅", "▅▃▇", "▇▂▅", "▅▇▃", "▃▅▆"]
ANIMATION_INTERVAL = 0.15


class NarratorStatus(NarratorManagerListener, Static):
    state = reactive(NarratorState.IDLE)

    def __init__(self, narrator_manager: NarratorManagerPort, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._narrator_manager = narrator_manager
        self._timer: Timer | None = None
        self._frame: int = 0
        self._last_width: int = 0

    def on_mount(self) -> None:
        self._narrator_manager.add_listener(self)

    def on_unmount(self) -> None:
        self._narrator_manager.remove_listener(self)

    def on_narrator_state_change(self, state: NarratorState) -> None:
        self.state = state

    def watch_state(self, new_state: NarratorState) -> None:
        self._stop_timer()
        match new_state:
            case NarratorState.IDLE:
                self._last_width = 0
                self.update("")
            case NarratorState.SUMMARIZING | NarratorState.SPEAKING:
                self._frame = 0
                self._tick()
                self._timer = self.set_interval(ANIMATION_INTERVAL, self._tick)

    def _tick(self) -> None:
        match self.state:
            case NarratorState.SUMMARIZING:
                char = SHRINK_FRAMES[self._frame % len(SHRINK_FRAMES)]
                self._update_frame(
                    f"[bold orange]{char}[/bold orange] summarizing "
                    f"[dim]{shortcut('Esc/Ctrl+C')} to stop[/dim]"
                )
            case NarratorState.SPEAKING:
                bars = BAR_FRAMES[self._frame % len(BAR_FRAMES)]
                self._update_frame(
                    f"[bold orange]{bars}[/bold orange] speaking "
                    f"[dim]{shortcut('Esc/Ctrl+C')} to stop[/dim]"
                )
        self._frame += 1

    def _update_frame(self, markup: str) -> None:
        content = Content.from_markup(markup)
        # Only relayout when the width changes. This status never wraps; equal
        # width does not imply equal rendered size for wrapped text.
        layout = content.cell_length != self._last_width
        self._last_width = content.cell_length
        self.update(content, layout=layout)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
