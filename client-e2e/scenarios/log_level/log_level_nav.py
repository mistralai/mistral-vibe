from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

# Down to INFO, then focus the config badge.
timeline: Timeline = ["/log-level", "\r", "\x1b[B", "\x1b[C", take_snapshot()]
