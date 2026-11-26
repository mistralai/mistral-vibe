from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = [
    "hello world",
    # Home, Right x5 (after "hello"), Ctrl+K deletes " world" -> "hello".
    "\x01\x1b[C\x1b[C\x1b[C\x1b[C\x1b[C\x0b",
    take_snapshot(),
]
