"""Enter on a task shows its latest output in the window, cleaned and scrolled to the end."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, DOWN, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "b", DOWN, "\r", take_snapshot("output")]

request_methods = ("session/backgroundProcess/output",)

screen_contains = (
    "uv run python watch_pdfs.py running",
    "batch-29.pdf",
    "indexed 36 PDFs",
    "progress 100%",
    "↑↓/jk scroll  s stop  / search  Esc back",
)
screen_excludes = (
    "batch-28",
    "progress 10%",
    "[32m",
    "Background tasks",
    "Search output",
)
