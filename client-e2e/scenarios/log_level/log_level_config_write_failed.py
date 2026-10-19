"""A rejected log-level write is reported as a failure and the config tier is restored."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"

handshake = {"config/write": {"rejected": True, "failures": []}}

capture_startup = False
# Down to ERROR, focus and set the config badge, apply, then reopen the picker.
timeline: Timeline = [
    "/log-level",
    "\r",
    "\x1b[B",
    "\x1b[C",
    "\r",
    _ESCAPE,
    "/log-level\r",
]

screen_contains = (
    "Failed to persist log level: Invalid configuration edit",
    "Effective: WARNING  (default)",
)
