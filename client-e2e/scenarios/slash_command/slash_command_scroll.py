from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

_DOWN = "\x1b[B"
_UP = "\x1b[A"


timeline: Timeline = ["/", _DOWN, _DOWN * 10, _UP * 11, _UP, take_snapshot()]
