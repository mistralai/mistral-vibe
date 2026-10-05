"""Rejected config mutations show an error and do not apply draft settings."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {"config/write": {"rejected": True, "failures": []}}
request_methods = {"telemetry/record"}
screen_contains = {
    "rust": (
        "Failed to apply: voice settings",
        "Invalid configuration edit",
        "Voice mode: On",
    )
}
screen_excludes = {"rust": ("Voice mode disabled.",)}
timeline: Timeline = ["/voice\r", " ", "\x1b", "/voice\r"]
