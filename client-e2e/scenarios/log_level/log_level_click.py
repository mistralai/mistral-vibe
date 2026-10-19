"""A click highlights a level, and a click on its badge toggles that badge."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Press and release in 1-based terminal coordinates: the INFO row, then its config badge.
_CLICK_INFO = "\x1b[<0;10;33M\x1b[<0;10;33m"
_CLICK_INFO_CONFIG = "\x1b[<0;30;33M\x1b[<0;30;33m"
_ESCAPE = "\x1b[27u"

timeline: Timeline = ["/log-level", "\r", _CLICK_INFO, _CLICK_INFO_CONFIG, _ESCAPE]
