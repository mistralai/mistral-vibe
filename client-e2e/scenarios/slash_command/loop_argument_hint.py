"""`/loop ` shows an argument hint, even with the caret moved back, until an argument is typed."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

capture_startup = False
screen_contains = {"rust": ("loop 5",)}
screen_excludes = {"rust": ("[schedule]",)}
_LEFT = "\x1b[D"
_RIGHT = "\x1b[C"

timeline: Timeline = ["/loop", " ", _LEFT, _RIGHT, "5"]
