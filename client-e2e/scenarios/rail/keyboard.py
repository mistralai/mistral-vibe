"""Ctrl+X swaps each rail glyph for its jump letter without moving anything; any key, even an arrow, hides the letters, and four Rights wrap back to the first item."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, RIGHT, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [
    *SETUP,
    CTRL_X,
    take_snapshot("agents"),
    *[RIGHT] * 4,
    take_snapshot("wrapped"),
]

screen_contains = ("◐ 1 agent · ▪ 2 tasks · ▶ goal 4m · ≡ 1/3",)
screen_excludes = ("s 1 agent",)
