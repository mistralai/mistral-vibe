"""Hovering then clicking the model on the input box's border opens /model over a draft; Esc brings the draft back."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The input box's top border is row 35; "mistral-medium-3.5[medium]" fills columns 78-103 of 120.
_ROW = 35
_HOVER = f"\x1b[<35;85;{_ROW}M"
_CLICK = f"\x1b[<0;85;{_ROW}M\x1b[<0;85;{_ROW}m"
_ESCAPE = "\x1b[27u"

timeline: Timeline = ["draft text", _HOVER, _CLICK, _ESCAPE]
screen_contains = ("draft text",)
screen_excludes = ("Select Model",)
