from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

_ESCAPE = "\x1b[27u"

timeline: Timeline = [
    "/theme",
    take_snapshot(),
    "\r",
    take_snapshot(),
    _ESCAPE,
    take_snapshot(),
    "x",
    take_snapshot(),
]
