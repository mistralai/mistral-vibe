"""Hovering a model row tints it with Textual's option hover background; the highlight keeps its bar."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# SGR button 35 is pointer motion with no button held, over the devstral-small row
# then the highlighted Default row, in 1-based terminal coordinates.
_HOVER_DEVSTRAL = "\x1b[<35;10;36M"
_HOVER_DEFAULT = "\x1b[<35;10;34M"

timeline: Timeline = ["/model", "\r", _HOVER_DEVSTRAL, _HOVER_DEFAULT]
