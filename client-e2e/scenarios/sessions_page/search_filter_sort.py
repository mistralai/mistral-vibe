"""`/` searches, `c` keeps this folder, `s` sorts by creation date; Esc clears then closes."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sessions_page._sessions import listed, session

_ESCAPE = "\x1b[27u"

handshake = listed(
    session(1, "Current work"),
    session(2, "Docs refresh", "/projects/website"),
    session(3, "Fix the login", "/projects/website"),
)

timeline: Timeline = [
    "/sessions",
    "\r",
    "/login",
    take_snapshot("search"),
    _ESCAPE,
    _ESCAPE,
    "c",
    take_snapshot("this_folder"),
    "c",
    "s",
    take_snapshot("by_creation"),
]
