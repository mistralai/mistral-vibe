"""`&<prompt>` teleports with the prompt and echoes it with the `&` marker."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.teleport import (
    METHODS,
    START,
    complete,
    handshake as teleport_handshake,
)

handshake = teleport_handshake()
request_methods = METHODS
on_request = {START: complete()}
timeline: Timeline = ["&", "fix the flaky test", "\r"]
