"""An easter egg stands in for `Generating` but never for a running command's status."""

from __future__ import annotations

from e2e.app_server.events import bash_started, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_EASTER_EGG": "Eating a chocolatine"}

screen_contains = ("Running command…",)
screen_excludes = ("Eating a chocolatine", "Generating")

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    bash_started("ls", "egg-bash"),
    {"release": 3},
    {"release": 1},
]
