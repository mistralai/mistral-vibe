"""One click on a theme row selects it, like Enter."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

# Press and release on the dracula row, in 1-based terminal coordinates.
_CLICK_DRACULA = "\x1b[<0;10;30M\x1b[<0;10;30m"

timeline: Timeline = [
    "/theme",
    take_snapshot(),
    "\r",
    take_snapshot(),
    _CLICK_DRACULA,
    take_snapshot(),
]
