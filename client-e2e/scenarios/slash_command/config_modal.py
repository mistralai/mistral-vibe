"""Settings modal filters and owns keyboard and wheel input."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_WHEEL_DOWN = "\x1b[<65;60;20M"

timeline: Timeline = ["/config\r", _WHEEL_DOWN * 3]
