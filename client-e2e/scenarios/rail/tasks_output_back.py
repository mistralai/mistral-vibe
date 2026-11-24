"""Esc in a task's output goes back to the task list, its highlight kept."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, DOWN, ESCAPE, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "b", DOWN, "\r", ESCAPE]

screen_contains = ("Background tasks 2", "▪ uv run python watch_pdfs.py", "Enter view")
screen_excludes = ("indexed 36 PDFs",)
