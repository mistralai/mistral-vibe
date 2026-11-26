"""`/cd alpha` moves the session into a subfolder, relative to the session cwd."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.cd._dirs import CLIENT_ARGS, REQUEST_METHODS, handshake, moved

handshake = handshake()
client_args = CLIENT_ARGS
request_methods = REQUEST_METHODS
on_request = moved()
# The move stays in the conversation as a status row, and the footer follows
# the session into `alpha`, below the pinned launch folder.
screen_contains = (
    "Moved from",
    "/tmp/vibe_e2e_fixtures/cd/alpha",
    "/test/workdir/alpha",
)

timeline: Timeline = ["/cd alpha\r", take_snapshot("moved")]
