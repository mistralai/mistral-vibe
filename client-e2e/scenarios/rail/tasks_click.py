"""Hovering a task row tints it, and a click only highlights it: `s` then stops that task."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake

__all__ = ["handshake"]

request_methods = ("session/backgroundProcess/stop",)

# The second task is row 37 of the window (1-based SGR coordinates).
_HOVER = "\x1b[<35;10;37M"
_CLICK = "\x1b[<0;10;37M\x1b[<0;10;37m"

timeline: Timeline = [*SETUP, CTRL_X, "b", _HOVER, take_snapshot("hover"), _CLICK, "s"]

screen_contains = ("Background tasks 1", "▪ kubectl get jobs -n orkl -w")
screen_excludes = ("watch_pdfs",)
