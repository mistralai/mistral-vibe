from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "ab",
    "\n",  # Ctrl+J: newline after "ab", caret drops to line 2
    "cd",  # lands on the second line -> "ab\ncd"
]
