"""A click highlights a model row and a second click selects it, like Enter."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Press and release on the devstral-small row, in 1-based terminal coordinates.
_CLICK_DEVSTRAL = "\x1b[<0;10;36M\x1b[<0;10;36m"

timeline: Timeline = ["/model", "\r", _CLICK_DEVSTRAL, _CLICK_DEVSTRAL]
