"""Sessions an agent started nest under their starter; Space unfolds them."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sessions_page._sessions import listed, session

_STARTER = "00000000-0000-4000-8000-000000000002"

handshake = listed(
    session(1, "Current work"),
    session(2, "Release plan"),
    session(3, "Changelog draft", startedBySessionId=_STARTER),
    session(4, "Docs pass", startedBySessionId=_STARTER),
)

timeline: Timeline = [
    "/sessions",
    "\r",
    "\x1b[B",
    take_snapshot("folded"),
    " ",
    take_snapshot("unfolded"),
]
