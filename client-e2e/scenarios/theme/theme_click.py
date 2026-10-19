"""One click on a theme row selects it, like Enter."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Press and release on the dracula row, in 1-based terminal coordinates.
_CLICK_DRACULA = "\x1b[<0;10;29M\x1b[<0;10;29m"

timeline: Timeline = ["/theme", "\r", _CLICK_DRACULA]
