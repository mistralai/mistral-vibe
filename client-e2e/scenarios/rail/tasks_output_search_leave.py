"""Esc in the output's focused search leaves the field and keeps the search and its place; the next Esc would clear it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, DOWN, ESCAPE, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "b", DOWN, "\r", "/", "batch-0", ESCAPE]

screen_contains = ("batch-09.pdf", "9/9", "n/N next/previous", "Esc clear search")
screen_excludes = ("progress 100%",)
