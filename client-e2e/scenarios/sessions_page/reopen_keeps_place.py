"""Esc then ← reopens the page where it was left, and ← after opening a session lands on it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sessions_page._sessions import listed, session

_LEFT = "\x1b[D"
_DOWN = "\x1b[B"
_RIGHT = "\x1b[C"
_ESCAPE = "\x1b[27u"

handshake = {
    **listed(
        session(1, "Current work"),
        session(2, "Docs refresh"),
        session(3, "Saved implementation"),
        session(4, "Older spike"),
    )
}
request_methods = {"session/resume"}

timeline: Timeline = [
    _LEFT,
    _DOWN,
    _DOWN,
    "s",
    _ESCAPE,
    _LEFT,
    take_snapshot("reopened"),
    # Back to the list order, onto the saved session the fixture resumes.
    "s",
    _RIGHT,
    _LEFT,
    take_snapshot("back_from_opened"),
]
