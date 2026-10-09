from __future__ import annotations

from e2e.app_server.scenario import Timeline

# /model, submit, navigate down, `s` selects model for this session only, then
# the thinking picker chains. `s` selects thinking level for this session only.
timeline: Timeline = ["/model", "\r", "\x1b[B", "s", "s"]
