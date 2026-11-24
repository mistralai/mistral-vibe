"""`.` shows archived sessions; `a` restores the highlighted one."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sessions_page._sessions import listed, session

handshake = {
    **listed(session(1, "Current work"), session(2, "Old spike", archivedAt=1)),
    "session/archive": {"archivedAt": None},
}
request_methods = {"session/archive"}

timeline: Timeline = [
    "/sessions",
    "\r",
    ".",
    take_snapshot("archived"),
    "a",
    take_snapshot("restored"),
]
