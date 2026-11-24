"""Esc leaves the output's search keeping it, a second Esc clears it, and a third goes back to the list."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, DOWN, ESCAPE, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [
    *SETUP,
    CTRL_X,
    "b",
    DOWN,
    "\r",
    "/",
    "indexed",
    ESCAPE,
    ESCAPE,
    ESCAPE,
]

screen_contains = ("Background tasks 2", "Enter view")
