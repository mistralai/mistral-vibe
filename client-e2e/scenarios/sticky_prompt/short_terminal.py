"""A short terminal keeps every transcript row for the conversation: no prompt is pinned."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize, take_snapshot
from scenarios.sticky_prompt._turns import tall_reply, turn

timeline: Timeline = [
    resize(22, 120),
    *turn("hi", tall_reply("a", 24)),
    take_snapshot(),
]

screen_excludes = ("> hi",)
