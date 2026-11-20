"""Ctrl+X always shows the letters: after closing a window with Esc, Ctrl+X letters the rail again."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, ESCAPE, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "t", ESCAPE, CTRL_X]

screen_contains = ("s 1 agent · b 2 tasks · g goal 4m · t 1/3",)
screen_excludes = ("Todo 1/3",)
