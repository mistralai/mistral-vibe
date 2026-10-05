"""Browsing queued prompts preserves both undo and redo for the current draft."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
expected_clipboard = "hello"
request_methods = {"telemetry/record"}

_UP = "\x1b[A"
_ESCAPE = "\x1b[27u"
_UNDO = "\x1b[122;9u"
_REDO = "\x1b[121;9u"

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    "hello",
    "\x7f\x7f\x7f",
    _UP,
    _UP,
    _ESCAPE,
    _UNDO,
    _UP,
    _UP,
    _ESCAPE,
    _REDO,
    _UNDO,
    "\x1b[18~\x1b[99;9u",
]
