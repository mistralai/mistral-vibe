from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = [
    "hello",
    "\x1b[18~\x7f",
    take_snapshot(),  # F7 select-all, Backspace clears -> ""
]
