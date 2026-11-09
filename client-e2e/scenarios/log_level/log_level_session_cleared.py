"""Toggling off an applied session override clears it and reports the cleared override."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"

capture_startup = False
request_methods = {"config/write"}

# Set ERROR for the session and apply; reopen on ERROR, toggle it off and apply.
timeline: Timeline = [
    "/log-level",
    "\r",
    "\x1b[B",
    "\r",
    _ESCAPE,
    "/log-level\r",
    "\r",
    _ESCAPE,
]

screen_contains = ("session override cleared  (effective: WARNING)",)
