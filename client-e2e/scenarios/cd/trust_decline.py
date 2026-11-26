"""Choosing "Don't trust" on the mid-session gate cancels `/cd` without persisting a decision."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.cd._dirs import CLIENT_ARGS, REQUEST_METHODS
from scenarios.cd._trust import trust_handshake

handshake = trust_handshake()
client_args = CLIENT_ARGS
request_methods = REQUEST_METHODS
screen_contains = ("Directory change cancelled.",)
screen_excludes = ("Moved to",)

timeline: Timeline = ["/cd alpha\r", "2"]
