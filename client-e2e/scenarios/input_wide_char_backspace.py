"""Backspace over wide graphemes keeps the remaining text; tests/units/wide_cells.rs guards the repaint pyte cannot show."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

screen_contains = ("> 萎萎萎",)
screen_excludes = ("萎萎萎萎",)

timeline: Timeline = ["萎" * 7, "\x7f" * 4]
