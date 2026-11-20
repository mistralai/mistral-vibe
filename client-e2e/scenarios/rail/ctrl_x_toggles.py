"""A non-rail key after Ctrl+X goes back to the prompt and types there; Right in the prompt never reaches the rail."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, LEFT, RIGHT, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, "ab", LEFT, RIGHT, RIGHT, "c", CTRL_X, CTRL_X, "d"]

screen_contains = ("> abcd",)
screen_excludes = ("t 1/3",)
