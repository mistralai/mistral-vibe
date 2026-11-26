"""Ctrl+X then `g` opens the goal window: the objective, its time and tokens, and the goal actions on the border."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "g", take_snapshot()]

screen_contains = (
    "▶ Goal active",
    "Make the parity suite pass",
    "4m · 12.5K / 50K tokens",
    "p pause  e edit  c clear  Esc close",
)
