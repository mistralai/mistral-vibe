"""The footer's context gauge stays clickable while a rail window replaces the input box."""

from __future__ import annotations

from e2e.app_server.context import handshake, stats_updated
from e2e.app_server.events import (
    assistant_msg,
    todo,
    todo_item,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

handshake = handshake()
request_methods = {"session/context/read"}

_ROW = 40
_WIDTH = 120
_GAUGE = "ctx █▎     42.3k/200k"
_CLICK_X = _WIDTH - len(_GAUGE) + 2
_CLICK = f"\x1b[<0;{_CLICK_X};{_ROW}M\x1b[<0;{_CLICK_X};{_ROW}m"
_CTRL_X = "\x18"

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    todo([todo_item("1", "Ship it", "pending")]),
    assistant_msg("Hi there."),
    stats_updated(),
    turn_completed(),
    _CTRL_X,
    "t",
    _CLICK,
]
screen_contains = ("auto-compact at",)
