"""Accepting a folder completion inserts `<dir>/` so the path can continue."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.cd._dirs import CLIENT_ARGS, REQUEST_METHODS, handshake

handshake = handshake()
client_args = CLIENT_ARGS
request_methods = REQUEST_METHODS
screen_contains = ("/ cd beta/",)

timeline: Timeline = ["/cd b", "\t"]
