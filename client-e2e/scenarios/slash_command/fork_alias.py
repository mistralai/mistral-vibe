"""`/fork` is an alias for `/branch` and forks a detached copy like it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._branch_fixtures import REQUEST_METHODS, fork_handshake

handshake = fork_handshake("unified")
request_methods = REQUEST_METHODS

screen_contains = (
    "Session forked",
    "Copy and paste this command to resume it:",
    "vibe --resume 22222222-0000-4000-8000-000000000002",
)
screen_excludes = ("Branched to a new session.",)
timeline: Timeline = ["/fork\r"]
