"""Ctrl+O expands the `Worked for` group together with the tool groups inside it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.turn_details._turns import HANDSHAKE, worked_turn

handshake = HANDSHAKE

timeline: Timeline = [*worked_turn(), "\x0f", take_snapshot()]

screen_contains = (
    "⏷ Worked for 1m 12s",
    "Let me look at the test first.",
    "cat test_auth.py",
)
