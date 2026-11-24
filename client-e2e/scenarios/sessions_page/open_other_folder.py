"""Opening a session from another folder resumes it there and the footer follows its folder."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sessions_page._sessions import listed, session

_OTHER = "00000000-0000-4000-8000-000000000003"

handshake = {
    **listed(
        session(1, "Current work"), session(3, "Website work", "/projects/website")
    ),
    "workspace/trust/status": {"details": None},
}
request_methods = {"session/resume", "session/markAsSeen"}

timeline: Timeline = ["/sessions", "\r", "\x1b[B", "\x1b[C", take_snapshot()]
