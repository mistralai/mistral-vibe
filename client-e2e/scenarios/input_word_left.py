from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = ["foo bar", "\x1b[1;5D\x7f", take_snapshot()]
