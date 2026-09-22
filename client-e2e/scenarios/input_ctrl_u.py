from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "hello world",
    # Left x3 puts the caret before "rld"; Ctrl+U deletes "hello wo" -> "rld".
    "\x1b[D\x1b[D\x1b[D\x15",
]
