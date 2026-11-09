"""`/branch` without a unified harness (legacy omits it) records `vibe.session_branched` from the client."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._branch_fixtures import REQUEST_METHODS, fork_handshake

handshake = fork_handshake(None)
request_methods = REQUEST_METHODS

capture_startup = False
screen_contains = ("Branched to a new session.",)
timeline: Timeline = ["/branch\r"]
