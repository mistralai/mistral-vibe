"""Dragging across the goal row selects and copies its text, like the loading row."""

from __future__ import annotations

from e2e.app_server.events import goal
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import started_with

env = {"SSH_TTY": "/dev/pts/0"}
handshake = started_with(goal())

# The goal row is row 34 of 40, under the loading area (1-based SGR coordinates).
_ROW = 34
_DRAG = f"\x1b[<0;3;{_ROW}M\x1b[<32;22;{_ROW}M\x1b[<0;22;{_ROW}m"

timeline: Timeline = [_DRAG, take_snapshot()]

clipboard_contains = ("Goal Make the parity",)
