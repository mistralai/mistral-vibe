"""Ctrl+X then `b` opens the background tasks window; `s` stops the highlighted task and the rail counts one less."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "b", take_snapshot("panel"), "s"]

request_methods = ("session/backgroundProcess/stop",)

screen_contains = (
    "Background tasks",
    "▪ uv run python watch_pdfs.py",
    "▪ 1 task · ▶ goal",
)
screen_excludes = ("kubectl get jobs",)
