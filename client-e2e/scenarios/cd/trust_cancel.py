"""Esc on the mid-session trust gate cancels `/cd`: no decision and no move is sent."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.cd._dirs import CLIENT_ARGS, REQUEST_METHODS
from scenarios.cd._trust import trust_handshake

handshake = trust_handshake()
client_args = CLIENT_ARGS
request_methods = REQUEST_METHODS
screen_contains = ("Directory change cancelled.",)
screen_excludes = ("Moved to", "Trust this folder?")

timeline: Timeline = ["/cd alpha\r", "\x1b"]
