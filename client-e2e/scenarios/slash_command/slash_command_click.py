"""Clicking inside a leading `/command` keeps the slash menu open, filtered up to the caret."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The composer's `/ sta` sits on SGR row 36; column 4 puts the caret after `/s`.
_CLICK = "\x1b[<0;4;36M\x1b[<0;4;36m"

timeline: Timeline = ["/sta", _CLICK]

capture_startup = False
settle_per_key = True
screen_contains = ("/ sta", "/status", "/stress")
