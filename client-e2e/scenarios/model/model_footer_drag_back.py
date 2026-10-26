"""A drag that starts on the footer's model and returns to its start cell is not a click: /model stays closed."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The footer is row 40; "mistral-medium-3.5[medium]" fills columns 74-99 of 120.
_ROW = 40
_PRESS = f"\x1b[<0;85;{_ROW}M"
_DRAG_AWAY = f"\x1b[<32;92;{_ROW}M"
_DRAG_BACK = f"\x1b[<32;85;{_ROW}M"
_RELEASE = f"\x1b[<0;85;{_ROW}m"

timeline: Timeline = [_PRESS + _DRAG_AWAY + _DRAG_BACK + _RELEASE]

screen_excludes = ("Select Model",)
