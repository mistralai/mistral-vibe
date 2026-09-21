"""Running titles and background completion follow focus acknowledgement."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    session_title_updated,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
capture_steps = {1, 2, 3, 4, 5, 6}

# An inert Right key requests a settled frame after each focus event.
timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    session_title_updated("Background task"),
    assistant_msg("Done."),
    turn_completed(),
    {"release": 3},
    "\x1b[O\x1b[C",
    {"release": 1},
    {"release": 2},
    "\x1b[I\x1b[C",
    "\x1b[O\x1b[C",
]

expected_titles = (
    "Vibe",
    ">> Vibe",
    ">> Vibe",
    ">> Background task",
    "Background task - Task Complete",
    "Background task",
    "Background task",
)
