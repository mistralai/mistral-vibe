"""A paste into the output's focused search searches like typing."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, DOWN, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [
    *SETUP,
    CTRL_X,
    "b",
    DOWN,
    "\r",
    "/",
    "\x1b[200~batch-03\x1b[201~",
]

screen_contains = ("batch-03.pdf", "1/1")
screen_excludes = ("progress 100%",)
