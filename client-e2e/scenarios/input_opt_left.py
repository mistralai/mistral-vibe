from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "foo bar",
    "\x1bb\x7f",  # ESC-b word-left to start of "bar", Backspace removes space -> "foobar"
]
