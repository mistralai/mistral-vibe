"""Expanding a long `Worked for` group scrolls to show as many steps as fit, its header kept on screen."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.turn_details._turns import HANDSHAKE, PROMPT

handshake = HANDSHAKE

_STEPS = [
    event
    for step in range(1, 9)
    for event in (
        assistant_msg(f"Step {step}: checking the next part of the suite."),
        bash(f"pytest tests/part_{step}.py", "1 passed"),
    )
]

_EXPAND = "\x1b[<0;5;11M\x1b[<0;5;11m"

timeline: Timeline = [
    f"{PROMPT}\r",
    turn_started(),
    user_msg(PROMPT),
    *_STEPS,
    assistant_msg("All parts pass."),
    turn_completed(),
    _EXPAND,
    take_snapshot(),
]

# The header lands under the pinned prompt and the steps fill the rest of the viewport.
screen_rows = {
    0: "> fix the flaky auth test",
    3: "⏷ Worked for <1s · 8 messages · 8 tool calls",
    5: "│   Step 1: checking the next part of the suite.",
}
screen_contains = ("Step 7: checking",)
