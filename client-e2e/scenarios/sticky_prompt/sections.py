"""The prompt of the section at the viewport top stays pinned, following the scroll across sections."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sticky_prompt._turns import WHEEL_UP, tall_reply, turn

timeline: Timeline = [
    *turn("hi", tall_reply("a", 24)),
    *turn("more", tall_reply("b", 24)),
    take_snapshot(),
    WHEEL_UP * 6,
    take_snapshot(),
    WHEEL_UP * 12,
    take_snapshot(),
    WHEEL_UP * 40,
    take_snapshot(),
]
