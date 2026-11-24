"""While the output's search is focused, letters and ←→ edit the query instead of acting or switching windows."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, DOWN, LEFT, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "b", DOWN, "\r", "/", "sk", LEFT, "\t", "s"]

request_methods = ("session/backgroundProcess/stop",)
screen_contains = ("uv run python watch_pdfs.py running", "no match")
