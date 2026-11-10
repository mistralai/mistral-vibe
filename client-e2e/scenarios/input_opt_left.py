from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = [
    "foo bar",
    "\x1bb\x7f",
    take_snapshot(),  # ESC-b word-left to start of "bar", Backspace removes space -> "foobar"
]
