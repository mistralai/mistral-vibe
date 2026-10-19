"""Picking Default from a pinned model saves an empty alias and unpins the model."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.model._pinned import MEDIUM, pinned_handshake

handshake = {
    **pinned_handshake(),
    "config/reload": {
        "runtime": {"config": {"activeModel": MEDIUM, "activeModelPinned": False}}
    },
}

capture_startup = False
request_methods = {"config/reload"}
# Up past mistral-medium-3.5 to Default, pick it and its level, then reopen /model.
timeline: Timeline = ["/model", "\r", "\x1b[A", "\x1b[A", "\r", "\r", "/model", "\r"]
screen_contains = ("Configuration reloaded",)
