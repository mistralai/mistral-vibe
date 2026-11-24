"""`r` opens a modal on the highlighted session's title: a long title wraps, a click moves the caret, Enter saves and the server's title replaces the row's."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sessions_page._sessions import listed, session

_LONG = "Docs refresh for the new resume flag and the Sessions page keys, with examples"

handshake = {
    **listed(session(1, "Current work"), session(2, _LONG)),
    "session/rename": {"title": "Docs v2", "updatedAt": 1, "lastEventId": 2},
}
request_methods = {"session/rename"}
screen_contains = ("Docs v2",)
screen_excludes = ("Rename session", "Docs refresh")

_DOWN = "\x1b[B"
_CTRL_U = "\x15"
# On the "w" of "with", where the title wraps onto its second row.
_CLICK_WITH = "\x1b[<0;29;21M\x1b[<0;29;21m"

timeline: Timeline = [
    "/sessions",
    "\r",
    _DOWN,
    "r",
    take_snapshot("wrapped"),
    _CLICK_WITH,
    "all ",
    take_snapshot("typed_at_click"),
    "\x1b[F",
    _CTRL_U,
    "Docs v2",
    "\r",
]
