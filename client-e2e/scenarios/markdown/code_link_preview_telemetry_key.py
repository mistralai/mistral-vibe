"""Scenario: a key closes the preview as `key`, the copy key still counting the copy out of it, and a plain key reaching the composer."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, advance

request_methods = {"telemetry/record"}
env = {"SSH_TTY": "/dev/pts/0"}
# Autocopy off: the selection is copied only by the copy key, after the key closed the preview.
handshake = {
    "config/read": {"config": {"autocopyToClipboard": False}},
    "runtime/read": {"runtime": {"config": {"autocopyToClipboard": False}}},
}
expected_clipboard = "def main() -> None:\n    names = sys.argv[1:] or [DEFAULT_NAME]"
screen_contains = ("> x",)

_PROMPT = "where is the entry point"
_ANSWER = "See [greet.py:12](code_preview/greet.py:12) for the entry point."

_HOVER = "\x1b[<35;10;11M"
_INTO_POPUP = "\x1b[<35;20;15M"
_OFF_LINK = "\x1b[<35;60;5M"
# Code starts at column 7 after the line numbers; lines 12 and 13 sit on rows 16 and 17.
_DRAG_TWO_LINES = "\x1b[<0;7;16M\x1b[<32;50;17M\x1b[<0;50;17m"
_COPY = "\x1b[99;9u"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _HOVER,
    advance(300),
    _INTO_POPUP,
    _DRAG_TWO_LINES,
    _COPY,
    _OFF_LINK,
    _HOVER,
    advance(300),
    advance(500),
    "x",
]
