from __future__ import annotations

from e2e.app_server.scenario import Timeline

# /model, submit, navigate down, Enter selects (persisted to config), then the
# thinking picker chains. Enter selects the current thinking level (persisted).
timeline: Timeline = ["/model", "\r", "\x1b[B", "\r", "\r"]
