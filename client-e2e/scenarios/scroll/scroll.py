from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_SHIFT_UP = "\x1b[1;2A"


def _hi_turn() -> Timeline:
    return [
        "hi\r",
        turn_started(),
        user_msg("hi"),
        assistant_msg("Hi. What do you need?"),
        turn_completed(),
    ]


timeline: Timeline = [*_hi_turn(), *_hi_turn(), *_hi_turn(), *_hi_turn(), _SHIFT_UP * 3]
