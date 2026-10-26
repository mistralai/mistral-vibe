"""A log level persisted during a running turn is written at once."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
screen_contains = ("config.toml → ERROR",)
screen_excludes = ("Slash commands cannot be queued",)
# Down to ERROR, focus and set the config badge, then apply.
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "/log-level",
    "\r",
    "\x1b[B",
    "\x1b[C",
    "\r",
    _ESCAPE,
]
