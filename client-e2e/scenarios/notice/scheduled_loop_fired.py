"""A fired loop's one-line prompt folds its fired-at line, which Ctrl+O reveals."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    scheduled_loop_fired,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"TZ": "UTC"}

screen_contains = ["⏷ Run the linter", "└ loop 1a2b3c4d - 2026-08-24 17:41:00 UTC"]
screen_excludes = ["Loop `1a2b3c4d` fired"]

timeline: Timeline = [
    "Run the linter\r",
    turn_started(),
    user_msg("Run the linter"),
    scheduled_loop_fired("Loop `1a2b3c4d` fired", "1a2b3c4d"),
    assistant_msg("Hello."),
    turn_completed(),
    "\x0f",
]
