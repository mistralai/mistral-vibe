"""Ctrl+X reaches the visible rail while `/context` fills the box: it closes like for a chip click and the letters show."""

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
_CLICK_CTX = f"\x1b[<0;{_CLICK_X};{_ROW}M\x1b[<0;{_CLICK_X};{_ROW}m"
_CTRL_X = "\x18"

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    todo([todo_item("1", "Ship it", "pending")]),
    assistant_msg("Hi there."),
    stats_updated(),
    turn_completed(),
    _CLICK_CTX,
    _CTRL_X,
]
screen_contains = ("t 0/1",)
screen_excludes = ("auto-compact at",)
