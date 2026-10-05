"""Accepted file mentions and pasted image placeholders are colored like skill mentions."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

capture_startup = False
capture_steps = {2, 3, 4}
settle_per_key = True
expected_clipboard = "read @README.md and [Image #1] "

timeline: Timeline = [
    "read @README.m",
    "\t",
    "and",
    paste("/tmp/vibe-shot.png"),
    "\x1b[18~\x19",
]
