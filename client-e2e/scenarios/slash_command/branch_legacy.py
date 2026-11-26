"""`/branch` without a unified harness (legacy omits it) records `vibe.session_branched` from the client."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._branch_fixtures import REQUEST_METHODS, fork_handshake

handshake = fork_handshake(None)
request_methods = REQUEST_METHODS

screen_contains = (
    "Session forked",
    "vibe --resume 22222222-0000-4000-8000-000000000002",
)
timeline: Timeline = ["/branch\r"]
