"""A second click on the footer's context gauge closes `/context`; pressing it selects no text."""

from __future__ import annotations

from e2e.app_server.context import handshake, stats_updated
from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

handshake = handshake()
request_methods = {"session/context/read"}

_ROW = 40
_WIDTH = 120
_GAUGE = "ctx █▎     42.3k/200k"
_CLICK_X = _WIDTH - len(_GAUGE) + 2
_PRESS = f"\x1b[<0;{_CLICK_X};{_ROW}M"
_RELEASE = f"\x1b[<0;{_CLICK_X};{_ROW}m"

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi there."),
    stats_updated(),
    turn_completed(),
    _PRESS + _RELEASE,
    # Held on the gauge: no selection cell is painted under the pointer.
    _PRESS,
    _RELEASE,
]
screen_contains = ("42.3k/200k",)
screen_excludes = ("auto-compact at",)
