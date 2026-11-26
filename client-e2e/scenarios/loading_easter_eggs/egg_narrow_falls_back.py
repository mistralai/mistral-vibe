"""When the egg would push the loading line onto an extra row, it shows `Generating` instead."""

from __future__ import annotations

from e2e.app_server.events import turn_started, user_msg
from e2e.app_server.scenario import Timeline, resize

env = {"VIBE_REPLAY_EASTER_EGG": "Eating a pain au chocolat"}

screen_contains = ("Generating…",)
screen_excludes = ("Eating a pain au chocolat",)

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    {"release": 3},
    resize(30, 46),
]
