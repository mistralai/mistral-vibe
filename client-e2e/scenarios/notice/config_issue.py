"""A startup skill config issue surfaces as a toast (VIBE-4622)."""

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

# Python surfaces config issues through Textual `App.notify`, whose toast mounts
# on a later frame and is not deterministically captured; Rust draws it inline.
skip_terminal_parity = (
    "python's Textual notify toast mounts async; rust draws it inline"
)

# A lone keystroke yields a settled frame; the startup toast is still up (10s).
timeline: Timeline = ["a"]

screen_contains = {"rust": ("Failed to load: bad frontmatter",)}
