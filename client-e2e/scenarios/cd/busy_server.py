"""A server that refuses the move because the session is busy shows the busy wording."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.cd._dirs import CLIENT_ARGS, REQUEST_METHODS, handshake

handshake = {
    **handshake(),
    "session/relocate": {
        "error": {
            "code": "conflict",
            "message": "A turn is already running: turn-1",
            "data": {"activeTurnId": "turn-1"},
        }
    },
}
client_args = CLIENT_ARGS
request_methods = REQUEST_METHODS
screen_contains = (
    "Slash commands cannot be queued — wait for the current job to finish.",
)
screen_excludes = ("Moved to",)

timeline: Timeline = ["/cd alpha\r"]
