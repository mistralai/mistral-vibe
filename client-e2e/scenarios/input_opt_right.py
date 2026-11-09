from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "foo bar",
    "\x01",  # Ctrl+A homes the caret (own step)
    "\x1bf\x7f",  # ESC-f word-right to end of "foo", Backspace removes its last char
]
