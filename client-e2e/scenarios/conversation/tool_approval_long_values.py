"""Keep long approval arguments fully inspectable."""

from __future__ import annotations

from e2e.app_server.events import (
    tool_approval,
    tool_approval_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
capture_startup = False
capture_steps = {1}
screen_contains = {
    "python": ("-PATTERN-TAIL", "/PATH-TAIL"),
    "rust": ("-PATTERN-TAIL", "/PATH-TAIL"),
}

_INPUT = {
    "pattern": "pattern-" + "x" * 150 + "-PATTERN-TAIL",
    "path": "folder/" + "y" * 150 + "/PATH-TAIL",
    "maxMatches": 25,
}

timeline: Timeline = [
    "search the long path\r",
    turn_started(),
    user_msg("search the long path"),
    tool_approval_added("grep", "file_search", _INPUT),
    tool_approval("grep", "file_search", _INPUT),
    {"release": 5},
]

settle_per_key = True
