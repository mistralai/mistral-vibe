"""On ansi-light a hovered button gets the option hover background and bold text; the context gauge keeps its track."""

from __future__ import annotations

from e2e.app_server.context import stats_updated
from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

env = {"VIBE_THEME": "ansi-light"}
handshake = {
    "config/read": {"config": {"theme": "ansi-light"}},
    "runtime/read": {"runtime": {"config": {"theme": "ansi-light"}}},
}

# The footer is row 40 and ends with the context gauge at column 120; the model sits on row 35's border.
_GAUGE = "ctx █▎     42.3k/200k"
_HOVER_GAUGE = f"\x1b[<35;{120 - len(_GAUGE) + 2};40M"
_HOVER_MODEL = "\x1b[<35;85;35M"

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi there."),
    stats_updated(),
    turn_completed(),
    take_snapshot(),
    _HOVER_GAUGE,
    take_snapshot(),
    _HOVER_MODEL,
    take_snapshot(),
]
screen_contains = ("42.3k/200k",)
