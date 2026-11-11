"""A long pinned prompt shows at most three rows, the last one cut with an ellipsis."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sticky_prompt._turns import tall_reply, turn

_PROMPT = " ".join(f"word{index:03d}" for index in range(1, 81))

timeline: Timeline = [*turn(_PROMPT, tall_reply("a", 24)), take_snapshot()]

screen_contains = ("> word001", "…")
