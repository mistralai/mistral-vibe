"""Unpushed commits ask before pushing; approving resumes the teleport."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.teleport import (
    METHODS,
    PUSH_RESPOND,
    START,
    complete,
    event,
    handshake as teleport_handshake,
)

env = {"VIBE_TYPING_GRACE_PERIOD_MS": "0", "VIBE_INPUT_GRACE_PERIOD_MS": "0"}
handshake = teleport_handshake()
request_methods = METHODS
on_request = {
    START: [event("checking_git"), event("push_required", unpushedCount=2)],
    PUSH_RESPOND: [event("pushing"), *complete()],
}
timeline: Timeline = ["&ship it\r", "\r"]
