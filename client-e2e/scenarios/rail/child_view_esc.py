"""Esc from a child view opened through the rail lands on the prompt: the next letter types instead of opening a window."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, ESCAPE, RIGHT, SETUP, handshake

__all__ = ["handshake"]

_DOWN = "\x1b[B"

timeline: Timeline = [*SETUP, CTRL_X, "s", _DOWN, "\r", RIGHT, ESCAPE, "g"]

screen_contains = ("> g",)
screen_excludes = ("▶ Goal active", "Background tasks")
