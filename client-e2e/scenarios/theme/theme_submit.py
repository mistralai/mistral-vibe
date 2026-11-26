from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = ["/them", take_snapshot(), "\r", take_snapshot()]
