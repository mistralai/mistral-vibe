from __future__ import annotations

from e2e.app_server.scenario import Timeline

expected_clipboard = "he"

_SELECT_RIGHT = "\x1b[1;2C"

timeline: Timeline = ["hello", f"\x01{_SELECT_RIGHT * 2}\x03"]
