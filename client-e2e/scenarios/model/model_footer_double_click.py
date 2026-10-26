"""Double-clicking the footer's model opens /model once and copies no word."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The footer is row 40; "mistral-medium-3.5[medium]" fills columns 74-99 of 120.
_ROW = 40
_CLICK = f"\x1b[<0;85;{_ROW}M\x1b[<0;85;{_ROW}m"

timeline: Timeline = [_CLICK * 2]

screen_contains = ("Select Model",)
screen_excludes = ("Copied",)
