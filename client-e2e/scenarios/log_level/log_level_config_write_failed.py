"""A rejected log-level write is reported on the inline notice, never as a row, and the config tier is restored."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, advance

_ESCAPE = "\x1b[27u"

handshake = {"config/write": {"rejected": True, "failures": []}}

# Down to ERROR, focus and set the config badge, apply, then reopen the picker.
timeline: Timeline = [
    "/log-level",
    "\r",
    "\x1b[B",
    "\x1b[C",
    "\r",
    _ESCAPE,
    advance(1500),
    "/log-level\r",
]

# The notice shows after Esc (snapshot 5); once read, typing the reopening command clears it.
screen_contains = ("Effective: WARNING  (default)",)
screen_excludes = ("✕", "Failed to persist log level")
