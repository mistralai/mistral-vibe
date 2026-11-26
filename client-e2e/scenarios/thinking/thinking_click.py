"""A click highlights a thinking level and a second click saves it, like Enter."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

# Press and release on the High row, in 1-based terminal coordinates.
_CLICK_HIGH = "\x1b[<0;10;36M\x1b[<0;10;36m"

timeline: Timeline = [
    "/thinking",
    take_snapshot(),
    "\r",
    take_snapshot(),
    _CLICK_HIGH,
    take_snapshot(),
    _CLICK_HIGH,
    take_snapshot(),
]
