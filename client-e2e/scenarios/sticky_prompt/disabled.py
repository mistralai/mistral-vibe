"""With `enable_sticky_prompt = false`, a scrolled section keeps its top rows: no prompt is pinned."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.sticky_prompt._turns import tall_reply, turn

handshake = {"runtime/read": {"runtime": {"config": {"enableStickyPrompt": False}}}}

timeline: Timeline = [*turn("hi", tall_reply("a", 24))]

capture_steps = {0}
screen_excludes = ("> hi",)
