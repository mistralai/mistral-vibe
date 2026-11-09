"""`/fork` is an alias for `/branch` and forks a detached copy like it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._branch_fixtures import REQUEST_METHODS, fork_handshake

handshake = fork_handshake("unified")
request_methods = REQUEST_METHODS

capture_startup = False
screen_contains = (
    "Branched to a new session.",
    "session: 00000000 → 22222222",
    "Resume the copy with: vibe --resume 22222222",
)
timeline: Timeline = ["/fork\r"]
