"""Word motion stopping inside a grapheme (keycap 1️⃣) still draws the caret on it."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

_HOME = "\x1b[H"
_CTRL_RIGHT = "\x1b[1;5C"

timeline: Timeline = [paste("1\ufe0f\u20e3 x"), _HOME + _CTRL_RIGHT]
