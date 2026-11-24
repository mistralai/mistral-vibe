"""A scripted easter egg replaces `Generating` on the loading line."""

from __future__ import annotations

from e2e.app_server.events import turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_EASTER_EGG": "Eating a chocolatine"}

screen_contains = ("Eating a chocolatine…",)
screen_excludes = ("Generating",)

timeline: Timeline = ["hi\r", turn_started(), user_msg("hi"), {"release": 3}]
