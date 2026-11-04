"""The first turn closes the startup warnings box: a later warning mounts as a row (VIBE-4099)."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    server_warning,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

handshake = {
    "runtime/read": {
        "runtime": {
            "issues": [
                {"file": ".vibe/hooks.toml", "message": "Failed to parse: bad toml"}
            ]
        }
    }
}

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hello."),
    turn_completed(),
    server_warning("Request too long: compacting the conversation."),
]

screen_contains = (
    "Warnings",
    "Failed to parse: bad toml",
    "⚠ Request too long: compacting the conversation.",
)
