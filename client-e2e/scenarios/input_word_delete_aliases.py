"""Ctrl and Option use the same backward and forward word deletions."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_CTRL_BACKSPACE = "\x1b[127;5u"
_OPTION_BACKSPACE = "\x1b\x7f"
_HOME = "\x01"
_CTRL_DELETE = "\x1b[3;5~"
_OPTION_DELETE = "\x1b[3;3~"

timeline: Timeline = [
    "one two three",
    _CTRL_BACKSPACE,
    _OPTION_BACKSPACE,
    _HOME,
    _CTRL_DELETE,
    "alpha beta",
    _HOME,
    _OPTION_DELETE,
]

capture_steps = {1, 2, 4, 7}
