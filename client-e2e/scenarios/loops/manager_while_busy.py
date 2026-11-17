"""`/loop` alone opens the manager while a turn runs; the turn keeps running behind it."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, scheduled_loop, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_SOON = scheduled_loop(
    "soon", "PR watch #42\nCheck the PR's CI.", in_seconds=290, interval_seconds=1_200
)
handshake = {
    "session/start": {"state": {"session": {"harness": "unified", "loops": [_SOON]}}}
}
request_methods = {"turn/interrupt", "session/turn/enqueue"}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "/loop\r",
]

screen_contains = ("Loops", "PR watch #42", "Esc close")
screen_excludes = ("Slash commands cannot be queued", "Queued")
