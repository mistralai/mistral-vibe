"""An empty bash submission reports an error instead of entering the turn queue."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
screen_contains = {
    "python": ("No command provided after '!'",),
    "rust": ("No command provided after '!'",),
}
# The first prompt is captured mid-enqueue, where the clients' hints diverge the
# same way `queue/first_prompt_hint` documents; the empty-bash error is the subject.
skip_terminal_parity = "Python reports an accepted prompt as queued until turn/started"

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "!",
    "\r",
]
