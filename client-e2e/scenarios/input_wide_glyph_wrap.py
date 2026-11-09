from __future__ import annotations

from e2e.app_server.scenario import Timeline

expected_clipboard = "c"
capture_startup = False
capture_steps = {1}

_UP = "\x1b[A"
_SELECT_RIGHT = "\x1b[1;2C"
_COPY = "\x03"

timeline: Timeline = ["ab" + "c" * 114 + "界", _UP + _SELECT_RIGHT + _COPY]
