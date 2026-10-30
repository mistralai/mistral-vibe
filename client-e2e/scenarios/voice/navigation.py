"""Voice settings wrap navigation and ignore composer input without saving."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

screen_excludes = ("should not be pasted", "Voice Settings", "Voice settings closed")
timeline: Timeline = [
    "/voice\r",
    "k",
    "j",
    "\x1b[A",
    "\x1b[B",
    "\x1b[200~should not be pasted\x1b[201~",
    "x\x12",
    "\x1b",
]
