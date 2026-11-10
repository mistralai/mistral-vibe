from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

_ESCAPE = "\x1b[27u"

timeline: Timeline = ["/model", "\r", _ESCAPE, "x", take_snapshot()]
