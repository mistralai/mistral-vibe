"""Open the first `/config` value editor with Enter."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = ["/config\r", "\r", take_snapshot()]
