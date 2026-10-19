"""Detected program status support keeps session titles free of activity indicators."""

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
terminal_responses = {"\x1b]7501;?\x1b\\": "\x1b]7501;?:future=ignored\x07"}
capture_steps = {1, 2, 3, 4, 5, 6}

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
    "Vibe",
    "Vibe",
    "Background task",
    "Background task",
    "Background task",
    "Background task",
)
