"""Dragging a selection past the output's top scrolls it, and the copy reaches the rows scrolled out of view."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, DOWN, SETUP, handshake

__all__ = ["handshake"]

env = {"SSH_TTY": "/dev/pts/0"}

# Row 30 shows batch-30; row 25 is above the window (1-based SGR coordinates).
_DRAG = "\x1b[<0;40;30M\x1b[<32;3;25M\x1b[<0;3;25m"

timeline: Timeline = [*SETUP, CTRL_X, "b", DOWN, "\r", _DRAG]

clipboard_contains = ("batch-26.pdf", "batch-30.pdf")
