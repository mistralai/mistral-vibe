"""The prompt of the section at the viewport top stays pinned, following the scroll across sections."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.sticky_prompt._turns import WHEEL_UP, tall_reply, turn

timeline: Timeline = [
    *turn("hi", tall_reply("a", 24)),
    *turn("more", tall_reply("b", 24)),
    WHEEL_UP * 6,
    WHEEL_UP * 12,
    WHEEL_UP * 40,
]

capture_steps = {1, 2, 3, 4}
