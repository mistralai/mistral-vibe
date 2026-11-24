"""A path that is not a folder is refused locally, without any request."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.cd._dirs import CLIENT_ARGS, REQUEST_METHODS, handshake

handshake = handshake()
client_args = CLIENT_ARGS
request_methods = REQUEST_METHODS
screen_contains = ("Not a directory:", "vibe_e2e_fixtures/cd/notes.md")

timeline: Timeline = ["/cd notes.md\r"]
