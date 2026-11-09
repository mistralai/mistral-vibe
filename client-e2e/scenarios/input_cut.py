from __future__ import annotations

from e2e.app_server.scenario import Timeline

expected_clipboard = "he"

timeline: Timeline = [
    "hello",
    "\x01\x1b[1;2C\x1b[1;2C\x18",  # home, select "he", cut -> "llo"
]
