"""A rail chip stays clickable while `/context` fills the box: the click closes it and opens the chip's window."""

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
from e2e.app_server.scenario import Timeline, take_snapshot

handshake = handshake()
request_methods = {"session/context/read"}

_ROW = 40
_WIDTH = 120
_GAUGE = "ctx █▎     42.3k/200k"
_CLICK_X = _WIDTH - len(_GAUGE) + 2
_CLICK_CTX = f"\x1b[<0;{_CLICK_X};{_ROW}M\x1b[<0;{_CLICK_X};{_ROW}m"
# With `/context` open the rail sits on row 23; the todo chip `≡ 0/1` ends a column before the edge.
_CLICK_TODO = "\x1b[<0;118;23M\x1b[<0;118;23m"

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    todo([todo_item("1", "Ship it", "pending")]),
    assistant_msg("Hi there."),
    stats_updated(),
    turn_completed(),
    _CLICK_CTX,
    take_snapshot("context"),
    _CLICK_TODO,
]
screen_contains = ("Todo 0/1", "Ship it")
screen_excludes = ("auto-compact at",)
