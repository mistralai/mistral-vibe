"""An untrusted target opens the trust gate mid-session; Enter trusts it, then the move runs."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.cd._dirs import CLIENT_ARGS, REQUEST_METHODS, moved
from scenarios.cd._trust import trust_handshake

handshake = trust_handshake()
client_args = CLIENT_ARGS
request_methods = REQUEST_METHODS
on_request = moved()
screen_contains = ("vibe_e2e_fixtures/cd/alpha",)

timeline: Timeline = ["/cd alpha\r", take_snapshot(), "\r"]
