"""ANSI-light link hover uses Textual's ANSI foreground/background binding."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_THEME": "ansi-light"}
handshake = {
    "config/read": {"config": {"theme": "ansi-light"}},
    "runtime/read": {"runtime": {"config": {"theme": "ansi-light"}}},
}
_PROMPT = "give me Paul Vezia's profile"
_ANSWER = "• Paul Vezia — Senior AI Engineer at Publicis. [LinkedIn profile](https://www.linkedin.com)"
_HOVER = "\x1b[<35;50;32M"
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _HOVER,
]
