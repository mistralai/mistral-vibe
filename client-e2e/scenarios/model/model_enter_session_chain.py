from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.model._session import session_handshake

handshake = session_handshake()

# /model, submit, navigate down, Enter selects model (persisted to config), then
# the thinking picker chains. `s` keeps the thinking level for this session only.
timeline: Timeline = ["/model", "\r", "\x1b[B", "\r", "s", take_snapshot()]
