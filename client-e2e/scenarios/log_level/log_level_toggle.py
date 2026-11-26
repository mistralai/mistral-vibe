from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

# Set the session badge on DEBUG; the effective marker must follow it.
timeline: Timeline = ["/log-level", "\r", "\r", take_snapshot()]
