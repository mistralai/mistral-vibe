"""Shift+Enter inserts a composer newline without submitting."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "first",
    "\x1b[13;2u",  # Shift+Enter in the kitty keyboard protocol
    "second",
]
