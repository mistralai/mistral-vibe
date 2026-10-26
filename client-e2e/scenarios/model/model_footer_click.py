"""Hovering then clicking the footer's model opens /model over a draft; Esc brings the draft back."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The footer is row 40; "mistral-medium-3.5[medium]" fills columns 74-99 of 120.
_ROW = 40
_HOVER = f"\x1b[<35;85;{_ROW}M"
_CLICK = f"\x1b[<0;85;{_ROW}M\x1b[<0;85;{_ROW}m"
_ESCAPE = "\x1b[27u"

timeline: Timeline = ["draft text", _HOVER, _CLICK, _ESCAPE]
screen_contains = ("draft text",)
screen_excludes = ("Select Model",)
