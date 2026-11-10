from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

# A pick only writes /theme; a stray config/reload would show up here.
request_methods = {"config/reload"}
timeline: Timeline = [
    "/theme",
    take_snapshot(),
    "\r",
    take_snapshot(),
    "\x1b[B",
    take_snapshot(),
    "\r",
    take_snapshot(),
]
