"""Sessions whose folder is gone stay hidden, from search too, until `g` shows them."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sessions_page._sessions import listed, session

_ESCAPE = "\x1b[27u"

handshake = listed(
    session(1, "Current work"),
    session(2, "Docs refresh", "/projects/website"),
    session(3, "Removed worktree", "/projects/app-old", cwdMissing=True),
    session(4, "Lost spike", "/projects/gone", cwdMissing=True),
)

timeline: Timeline = [
    "/sessions",
    "\r",
    take_snapshot("hidden"),
    "/worktree",
    take_snapshot("search_hidden"),
    _ESCAPE,
    _ESCAPE,
    "g",
    take_snapshot("shown"),
]
