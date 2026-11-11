"""A fired loop prompt shows its title and folds its instructions and loop line behind Ctrl+O or a click."""

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

screen_contains = ["⏷ CI watch", "└ loop 1a2b3c4d - 2026-08-24 17:41:00 UTC"]

timeline: Timeline = [
    "watch CI every minute\r",
    turn_started(),
    user_msg("watch CI every minute"),
    assistant_msg("Scheduled."),
    turn_completed(),
    turn_started(),
    user_msg("CI watch\nCheck CI and report failures.\nPost a summary."),
    scheduled_loop_fired("Loop `1a2b3c4d` fired", "1a2b3c4d"),
    assistant_msg("CI is green."),
    turn_completed(),
    "\x0f",
    "\x1b[<0;3;14M\x1b[<0;3;14m",
    # With text selected, a click on the title still unfolds the prompt.
    "\x1b[<0;3;17M\x1b[<32;5;17M\x1b[<0;5;17m",
    "\x1b[<0;3;14M\x1b[<0;3;14m",
]
