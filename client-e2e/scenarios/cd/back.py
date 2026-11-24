"""`/cd -` returns to the folder the last `/cd` left."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.cd._dirs import CLIENT_ARGS, REQUEST_METHODS, handshake, moved

handshake = handshake("alpha", "")
client_args = CLIENT_ARGS
request_methods = REQUEST_METHODS
on_request = moved()
screen_contains = ("Moved from",)

timeline: Timeline = ["/cd alpha\r", "/cd -\r"]
