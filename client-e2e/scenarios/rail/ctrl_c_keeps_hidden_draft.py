"""Ctrl+C over a rail window closes it and keeps the draft the window hid."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, "secret draft", CTRL_X, "t", "\x03"]

screen_contains = ("secret draft",)
screen_excludes = ("Todo 1/3",)
