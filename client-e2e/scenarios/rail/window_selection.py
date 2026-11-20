"""Dragging across the goal window's text selects and copies it, like the loading row."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake

__all__ = ["handshake"]

env = {"SSH_TTY": "/dev/pts/0"}

# The objective is row 36, inside the window's left border (1-based SGR coordinates).
_DRAG = "\x1b[<0;3;36M\x1b[<32;22;36M\x1b[<0;22;36m"

timeline: Timeline = [*SETUP, CTRL_X, "g", _DRAG, take_snapshot()]

clipboard_contains = ("Make the parity",)
