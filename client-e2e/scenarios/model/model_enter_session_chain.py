from __future__ import annotations

from e2e.app_server.scenario import Timeline

# /model, submit, navigate down, Enter selects model (persisted to config), then
# the thinking picker chains. `s` keeps the thinking level for this session only.
timeline: Timeline = ["/model", "\r", "\x1b[B", "\r", "s"]
