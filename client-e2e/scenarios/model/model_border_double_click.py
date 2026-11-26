"""Double-clicking the input box's model opens /model once and copies no word."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The input box's top border is row 35; "mistral-medium-3.5[medium]" fills columns 78-103 of 120.
_ROW = 35
_CLICK = f"\x1b[<0;85;{_ROW}M\x1b[<0;85;{_ROW}m"

timeline: Timeline = [_CLICK * 2]

screen_contains = ("Select Model",)
screen_excludes = ("Copied",)
