"""Enter keeps the search; n and N move between its matches, wrapping, and the view follows."""

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
    "batch-1",
    "\r",
    "N",
    "n",
    "n",
]

screen_contains = ("batch-10.pdf", "1/10", "n/N next/previous", "Esc clear search")
screen_excludes = ("progress 100%",)
