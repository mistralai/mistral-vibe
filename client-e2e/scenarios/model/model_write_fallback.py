"""An app-server without config/model/write still saves a /model pick through config/write."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

capture_startup = False
request_methods = {"config/reload"}
handshake = {
    "config/model/write": {
        "error": {"code": "method_not_found", "message": "Unknown method"}
    }
}

timeline: Timeline = ["/model", "\r", "\x1b[B", "\r", "\r"]

screen_contains = ("Configuration reloaded",)
screen_excludes = ("Failed to apply",)
