"""In-band persistence failures surface without applying draft settings."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "config/write": {"rejected": False, "failures": ["Settings are read-only"]}
}
request_methods = {"telemetry/record"}
screen_contains = {
    "rust": (
        "Failed to apply: voice settings",
        "Settings are read-only",
        "Voice mode: On",
    )
}
screen_excludes = {"rust": ("Voice mode disabled.",)}
timeline: Timeline = ["/voice\r", " ", "\x1b", "/voice\r"]
