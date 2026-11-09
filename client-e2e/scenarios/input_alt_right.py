from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "foo bar",
    "\x01",  # Ctrl+A homes the caret (own step)
    "\x1b[1;3C\x7f",  # Alt+Right to end of "foo", Backspace removes its last char
]
