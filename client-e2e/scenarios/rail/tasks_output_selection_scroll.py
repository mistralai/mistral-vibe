"""A selection in a task's output stays on its text while the output scrolls, and copies it whole."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, DOWN, SETUP, handshake

__all__ = ["handshake"]

env = {"SSH_TTY": "/dev/pts/0"}

# batch-29 and batch-30 are the first two text rows, 29 and 30 (1-based SGR coordinates).
_DRAG = "\x1b[<0;3;29M\x1b[<32;40;30M\x1b[<0;40;30m"

timeline: Timeline = [*SETUP, CTRL_X, "b", DOWN, "\r", _DRAG, "k", "k", take_snapshot()]

expected_clipboard = "scanning invoices/batch-29.pdf\nscanning invoices/batch-30.pdf"
