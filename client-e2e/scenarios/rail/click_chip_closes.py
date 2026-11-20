"""A second click on the chip whose window is open closes that window."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import SETUP, handshake

__all__ = ["handshake"]

_CLICK_TODO = "\x1b[<0;116;34M\x1b[<0;116;34m"

timeline: Timeline = [*SETUP, _CLICK_TODO, _CLICK_TODO, "typed"]

screen_contains = ("> typed",)
screen_excludes = ("Todo 1/3",)
