"""A paste while a picker hides the composer is dropped, not kept for later."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

capture_startup = False
screen_excludes = ("pasted while picking",)

timeline: Timeline = [
    "/theme\r",
    "\x1b[O" + paste("pasted while picking") + "\x1b[I",
    "\x1b[27u",
]
