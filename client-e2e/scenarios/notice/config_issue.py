"""A startup skill config issue lands in the startup warnings box (VIBE-4099)."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The config issue rides the startup runtime snapshot, not a server event.
handshake = {
    "runtime/read": {
        "runtime": {
            "issues": [
                {
                    "file": ".vibe/skills/broken/SKILL.md",
                    "message": "Failed to load: bad frontmatter",
                }
            ]
        }
    }
}

# A lone keystroke yields a settled frame.
timeline: Timeline = ["a"]

screen_contains = ("Failed to load: bad frontmatter",)
