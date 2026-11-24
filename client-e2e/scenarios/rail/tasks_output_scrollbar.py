"""Overflowing output shows a scrollbar; dragging its thumb to the top shows the first line."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, DOWN, SETUP, handshake

__all__ = ["handshake"]

# The thumb covers row 37 of column 118; the drag ends above the track, on the top border (1-based SGR coordinates).
_DRAG = "\x1b[<0;118;37M\x1b[<32;118;27M\x1b[<0;118;27m"

timeline: Timeline = [*SETUP, CTRL_X, "b", DOWN, "\r", _DRAG]

screen_contains = ("scanning invoices/batch-01.pdf",)
screen_excludes = ("progress 100%",)
