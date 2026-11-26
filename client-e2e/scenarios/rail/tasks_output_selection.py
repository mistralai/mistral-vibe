"""A drag over the output to the window's right edge copies the text, never the scrollbar beside it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, DOWN, SETUP, handshake

__all__ = ["handshake"]

env = {"SSH_TTY": "/dev/pts/0"}

# The last two output rows are 37 and 38; the scrollbar is column 118 (1-based SGR coordinates).
_DRAG = "\x1b[<0;3;37M\x1b[<32;118;38M\x1b[<0;118;38m"

timeline: Timeline = [*SETUP, CTRL_X, "b", DOWN, "\r", _DRAG]

clipboard_contains = ("indexed 36 PDFs", "progress 100%")
clipboard_excludes = ("█", "▂")
