"""Ctrl+Y with nothing selected yanks back the text the last Ctrl+K removed."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = [
    "hello world",
    # Home, Right x6 (after "hello "), Ctrl+K kills "world" -> "hello ".
    "\x01" + "\x1b[C" * 6 + "\x0b",
    # Home, Ctrl+Y yanks "world" at the caret, then a space -> "world hello ".
    "\x01\x19 ",
    take_snapshot(),
]
