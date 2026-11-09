"""Cutting a selection followed by the same text copies the selection itself."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

expected_clipboard = "the "

timeline: Timeline = [
    "x the the",
    # home, two steps right, select "the ", cut -> "x the"
    "\x01\x1b[C\x1b[C\x1b[1;2C\x1b[1;2C\x1b[1;2C\x1b[1;2C\x18",
]
