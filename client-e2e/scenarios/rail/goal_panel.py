"""Ctrl+X then `g` opens the goal window and hides the letters; Esc comes back to the rail on the goal."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, ESCAPE, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "g", ESCAPE]

screen_contains = ("▪ 2 tasks · ▶ goal",)
# On the rail the interrupt hint drops Esc, which the rail takes.
screen_excludes = ("Goal active", "Esc/Ctrl+C")
