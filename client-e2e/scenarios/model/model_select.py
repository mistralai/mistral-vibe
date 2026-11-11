from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = ["/model", "\r", "\x1b[B", "\r", take_snapshot()]
