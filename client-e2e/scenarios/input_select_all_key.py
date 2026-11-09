from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "hello",
    "\x1b[18~\x7f",  # F7 select-all, Backspace clears -> ""
]
