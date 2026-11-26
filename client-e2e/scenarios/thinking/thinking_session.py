from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.model._session import session_handshake

handshake = session_handshake()

# /thinking, submit, navigate down, `s` selects thinking level for this session
# only (overrides layer).
timeline: Timeline = [
    "/thinking",
    take_snapshot(),
    "\r",
    take_snapshot(),
    "\x1b[B",
    take_snapshot(),
    "s",
    take_snapshot(),
]
