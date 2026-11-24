"""A second Esc clears the output's search and hides its row, keeping the output open where it was."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, DOWN, ESCAPE, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "b", DOWN, "\r", "/", "batch-0", ESCAPE, ESCAPE]

screen_contains = ("batch-09.pdf", "/ search", "Esc back")
screen_excludes = ("9/9", "Search output", "Esc clear search")
