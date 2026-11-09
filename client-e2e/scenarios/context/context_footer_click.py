"""Clicking the footer's context gauge opens the same `/context` app as the typed command; Esc closes it."""

from __future__ import annotations

from e2e.app_server.context import handshake, stats_updated
from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

handshake = handshake()
request_methods = {"session/context/read"}

# The footer is row 40; the right side is the context gauge, ending at column 120.
_ROW = 40
_WIDTH = 120
_GAUGE = "ctx █▎     42.3k/200k"
_CLICK_X = _WIDTH - len(_GAUGE) + 2
_HOVER = f"\x1b[<35;{_CLICK_X};{_ROW}M"
_CLICK = f"\x1b[<0;{_CLICK_X};{_ROW}M\x1b[<0;{_CLICK_X};{_ROW}m"
_ESCAPE = "\x1b[27u"

# The golden SVGs capture the open app per step; the last frame (after Esc) asserts the footer counter.
timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi there."),
    stats_updated(),
    turn_completed(),
    _HOVER,
    _CLICK,
    _ESCAPE,
]
screen_contains = ("42.3k/200k",)
screen_excludes = ("auto-compact at",)
