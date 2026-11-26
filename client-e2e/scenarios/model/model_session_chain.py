from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.model._session import session_handshake

handshake = session_handshake()
# A session-only pick records the same selection as a saved one.
request_methods = {"telemetry/record"}

# /model, submit, navigate down, `s` selects model for this session only, then
# the thinking picker chains. `s` selects thinking level for this session only.
timeline: Timeline = ["/model", "\r", "\x1b[B", "s", "s", take_snapshot()]
