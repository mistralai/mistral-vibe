"""With `collapse_turn_details`, a settled turn shows a `Worked for` header above its final answer."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.turn_details._turns import HANDSHAKE, worked_turn

handshake = HANDSHAKE

timeline: Timeline = [*worked_turn()]

screen_contains = (
    "Worked for 1m 12s · 2 messages · 3 tool calls",
    "Fixed: the token now refreshes before the retry.",
)
screen_excludes = ("Let me look at the test first.", "pytest test_auth.py")
