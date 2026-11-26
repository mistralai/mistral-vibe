"""An applied /model pick is not followed by config/reload, which a prompt sent right after would refuse."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

request_methods = {"config/reload"}
# The prompt's turn started while the reload was in flight (VIBE-5617).
handshake = {
    "config/reload": {
        "error": {"code": "conflict", "message": "A turn is already running: turn-1"}
    }
}

timeline: Timeline = ["/model", "\r", "\x1b[B", "\r", "\r"]

screen_contains = ("Applied: model mistral-medium-3.5, thinking medium.",)
screen_excludes = ("Failed to apply", "A turn is already running")
