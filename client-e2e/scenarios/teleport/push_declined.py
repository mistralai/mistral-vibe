"""Escaping the push question declines it and surfaces the server's error."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.teleport import (
    METHODS,
    PUSH_RESPOND,
    START,
    event,
    failed,
    handshake as teleport_handshake,
)

env = {"VIBE_TYPING_GRACE_PERIOD_MS": "0", "VIBE_INPUT_GRACE_PERIOD_MS": "0"}
handshake = teleport_handshake()
request_methods = METHODS
on_request = {
    START: [event("push_required", unpushedCount=1, branchNotPushed=True)],
    PUSH_RESPOND: [failed("Teleport cancelled: changes not pushed.")],
}
timeline: Timeline = ["/teleport\r", "\x1b"]
