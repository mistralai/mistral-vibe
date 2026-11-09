"""Turning `enable_sticky_prompt` off mid-session unpins the prompt without a restart."""

from __future__ import annotations

from e2e.app_server.events import runtime_updated
from e2e.app_server.scenario import Timeline
from scenarios.sticky_prompt._turns import tall_reply, turn

_update = runtime_updated()
_update["params"]["runtime"]["config"]["enableStickyPrompt"] = False

capture_steps = {1, 2}
screen_excludes = ("> hi",)

# The settled turn pins its prompt, then the update turns it off.
timeline: Timeline = [
    *turn("hi", tall_reply("a", 24)),
    _update,
    {"release": 5},
    {"release": 1},
]
