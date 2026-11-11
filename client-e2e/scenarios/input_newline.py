from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = [
    "ab",
    "\n",  # Ctrl+J: newline after "ab", caret drops to line 2
    "cd",
    take_snapshot(),  # lands on the second line -> "ab\ncd"
]
