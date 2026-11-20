"""A message steered into the turn stays outside its `Worked for` group."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.turn_details._turns import HANDSHAKE

handshake = HANDSHAKE

_STEER = user_msg("also check the docs", entry_id="steer-1")
_STEER["params"]["entry"]["source"] = "turn_steer"

timeline: Timeline = [
    "fix the flaky auth test\r",
    turn_started(),
    user_msg("fix the flaky auth test"),
    assistant_msg("Let me look at the test first."),
    bash("pytest test_auth.py", "1 failed"),
    _STEER,
    bash("cat docs/auth.md", "# Auth"),
    assistant_msg("Fixed, and the docs match."),
    turn_completed(),
    take_snapshot(),
]

screen_contains = (
    "> also check the docs",
    "Worked for <1s · 1 message · 2 tool calls",
    "Fixed, and the docs match.",
)
screen_excludes = ("Let me look at the test first.", "cat docs/auth.md")
