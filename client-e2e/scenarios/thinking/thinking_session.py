from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.model._session import session_handshake

handshake = session_handshake()

# /thinking, submit, navigate down, `s` selects thinking level for this session
# only (overrides layer).
timeline: Timeline = ["/thinking", "\r", "\x1b[B", "s"]
