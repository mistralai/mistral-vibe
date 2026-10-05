"""A failed teleport replaces its status row with the error."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.teleport import (
    METHODS,
    START,
    event,
    failed,
    handshake as teleport_handshake,
)

handshake = teleport_handshake()
request_methods = METHODS
on_request = {
    START: [event("checking_git"), failed("Git repository has uncommitted changes")]
}
timeline: Timeline = ["/teleport\r"]
