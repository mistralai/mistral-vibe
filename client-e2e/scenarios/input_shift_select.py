from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = [
    "hello",
    "\x1b[1;2D\x1b[1;2D\x7f",
    take_snapshot(),  # select the last two chars, Backspace deletes them
]
