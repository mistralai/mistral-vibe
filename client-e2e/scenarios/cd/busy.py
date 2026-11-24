"""`/cd` while a turn runs is refused and kept in the composer, like every idle-only command."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline
from scenarios.cd._dirs import CLIENT_ARGS, REQUEST_METHODS, handshake

handshake = {
    **handshake(),
    "workspace/prompt/prepare": {"prompt": {"promptText": None}},
}
client_args = CLIENT_ARGS
request_methods = REQUEST_METHODS
screen_contains = ("Slash commands cannot be queued", "/ cd alpha")
screen_excludes = ("Moved to",)

timeline: Timeline = [
    "long task\r",
    turn_started(),
    user_msg("long task"),
    assistant_msg("Working on it."),
    {"release": 4},
    "/cd alpha\r",
]
