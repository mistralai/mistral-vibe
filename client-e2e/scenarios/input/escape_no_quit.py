from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

_ESCAPE = "\x1b[27u"

timeline: Timeline = ["hello", _ESCAPE, "world", take_snapshot()]
