"""A startup config issue stays in the transcript above a modal picker (VIBE-4099)."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The config issue rides the startup runtime snapshot, not a server event.
handshake = {
    "runtime/read": {
        "runtime": {
            "issues": [
                {"file": ".vibe/hooks.toml", "message": "Failed to parse: bad toml"}
            ]
        }
    }
}

# Open the `/theme` picker; the warnings box stays in the transcript above it.
timeline: Timeline = ["/theme", "\r"]

screen_contains = ("Failed to parse: bad toml",)
