"""A pinned prompt a scheduled loop sent shows only its folded title, without its instructions or loop line."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    scheduled_loop_fired,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sticky_prompt._turns import WHEEL_UP, tall_reply

env = {"TZ": "UTC"}

timeline: Timeline = [
    "Say hi\r",
    turn_started(),
    user_msg("Say hi\nSay hi to the user."),
    scheduled_loop_fired("Loop `ae1aa08a` fired", "ae1aa08a"),
    assistant_msg(tall_reply("a", 24)),
    turn_completed(),
    WHEEL_UP * 6,
    take_snapshot(),
]

screen_rows = {0: "⏵ Say hi"}
screen_excludes = ("Say hi to the user.", "loop ae1aa08a")
