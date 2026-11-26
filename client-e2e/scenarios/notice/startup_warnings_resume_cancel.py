"""Cancelling the startup resume picker keeps the startup Warnings box (VIBE-4099)."""

from __future__ import annotations

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

_ESCAPE = "\x1b[27u"

client_args = ("--resume",)
timeline: Timeline = [_ESCAPE]

screen_contains = ("Warnings", "Failed to parse: bad toml")
