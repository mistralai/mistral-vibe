"""Two Shift+Enter presses create two blank composer rows."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SHIFT_ENTER = "\x1b[13;2u"

timeline: Timeline = ["lala", _SHIFT_ENTER, _SHIFT_ENTER, "lala"]
