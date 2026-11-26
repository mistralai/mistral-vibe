"""A resize rewraps the output and keeps the search's current match and its place among the others."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize
from scenarios.rail._fixtures import CTRL_X, DOWN, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [
    *SETUP,
    CTRL_X,
    "b",
    DOWN,
    "\r",
    "/",
    "batch-2",
    "\r",
    resize(40, 30),
]

screen_contains = ("10/10",)
screen_excludes = ("no match",)
