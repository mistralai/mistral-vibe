"""A click highlights a task and a second click on it shows its output."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake

__all__ = ["handshake"]

request_methods = ("session/backgroundProcess/output",)

# The second task is row 37 of the window (1-based SGR coordinates).
_CLICK = "\x1b[<0;10;37M\x1b[<0;10;37m"

timeline: Timeline = [*SETUP, CTRL_X, "b", _CLICK, _CLICK]

screen_contains = ("uv run python watch_pdfs.py running", "indexed 36 PDFs")
