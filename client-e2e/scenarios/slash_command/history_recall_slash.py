from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_UP = "\x1b[A"
_ESCAPE = "\x1b[27u"

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    "/theme",
    "\r",
    _ESCAPE,
    _UP,
    _UP,
]
