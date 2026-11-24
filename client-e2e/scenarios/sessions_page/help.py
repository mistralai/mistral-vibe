"""`?` lists every key of the Sessions page; Esc goes back to the list."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = [
    "/sessions",
    "\r",
    "?",
    take_snapshot("help"),
    "\x1b[27u",
    take_snapshot("list"),
]
