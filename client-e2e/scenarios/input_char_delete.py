from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "abcd",
    "\x1b[D\x7f",  # Left (before 'd'), Backspace deletes 'c' -> "abd"  # typos:disable-line
    "\x01\x1b[3~",  # Home, Delete removes 'a' -> "bd"
]
