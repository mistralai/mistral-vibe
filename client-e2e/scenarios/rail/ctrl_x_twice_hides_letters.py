"""Ctrl+X again while the letters show hides them, like any other key, and the rail keeps focus."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, CTRL_X]

screen_contains = ("◐ 1 agent · ▪ 2 tasks · ▶ goal 4m · ≡ 1/3",)
# On the rail the interrupt hint drops Esc, which the rail takes.
screen_excludes = ("s 1 agent", "Esc/Ctrl+C")
