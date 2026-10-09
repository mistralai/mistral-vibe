from __future__ import annotations

from e2e.app_server.scenario import Timeline

# /model, submit, navigate down, `s` selects for this session only (overrides
# layer), then the thinking picker chains automatically.
timeline: Timeline = ["/model", "\r", "\x1b[B", "s"]
