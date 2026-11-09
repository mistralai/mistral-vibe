from __future__ import annotations

from e2e.app_server.scenario import Timeline

# /thinking, submit, navigate down, `s` selects thinking level for this session
# only (overrides layer).
timeline: Timeline = ["/thinking", "\r", "\x1b[B", "s"]
