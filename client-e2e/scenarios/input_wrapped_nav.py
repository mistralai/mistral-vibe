"""Arrow keys move between visual rows of a soft-wrapped composer line."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_TEXT = "0123456789 " * 12
_UP = "\x1b[A"
_DOWN = "\x1b[B"

timeline: Timeline = [_TEXT, _UP, "X", _DOWN, "Y"]
