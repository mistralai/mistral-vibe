"""Clicking the pinned prompt scrolls the transcript back to that prompt."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.sticky_prompt._turns import tall_reply, turn

_CLICK = "\x1b[<0;30;1M\x1b[<0;30;1m"

timeline: Timeline = [
    *turn("hi", tall_reply("a", 4)),
    *turn("more", tall_reply("b", 24)),
    _CLICK,
]

capture_steps = {1, 2}
screen_contains = ("> more", "b01", "Jump to bottom")
