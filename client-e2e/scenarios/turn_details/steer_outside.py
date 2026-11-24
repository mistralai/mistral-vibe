"""A steer splits the turn: a `Worked for` group before it, one after it, the steer marked `↳` between them."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.turn_details._turns import HANDSHAKE, at

handshake = HANDSHAKE

_STEER = at(user_msg("also check the docs", entry_id="steer-1"), 1_787_593_270_000)
_STEER["params"]["entry"]["source"] = "turn_steer"

timeline: Timeline = [
    "fix the flaky auth test\r",
    turn_started(),
    at(user_msg("fix the flaky auth test"), 1_787_593_260_000),
    assistant_msg("Let me look at the test first."),
    bash("pytest test_auth.py", "1 failed"),
    _STEER,
    bash("cat docs/auth.md", "# Auth"),
    at(assistant_msg("Fixed, and the docs match."), 1_787_593_332_500),
    turn_completed(),
    take_snapshot(),
]

screen_rows = {
    10: "⏵ Worked for 10s · 1 message · 1 tool call",
    11: "",
    12: "↳ also check the docs",
    13: "",
    14: "⏵ Worked for 1m 2s · 1 tool call",
    15: "",
    16: "  Fixed, and the docs match.",
}
