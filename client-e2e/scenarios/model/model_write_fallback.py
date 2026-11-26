"""An app-server without config/model/write still saves a /model pick through config/write."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.model._session import session_handshake

request_methods = {"config/reload"}
handshake = {
    **session_handshake(),
    "config/model/write": {
        "error": {"code": "method_not_found", "message": "Unknown method"}
    },
}

timeline: Timeline = ["/model", "\r", "\x1b[B", "\r", "\r"]

screen_contains = ("Applied: model mistral-medium-3.5, thinking medium.",)
screen_excludes = ("Failed to apply",)
