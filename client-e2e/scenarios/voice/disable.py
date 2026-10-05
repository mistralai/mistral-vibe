"""Saved voice settings immediately disable recording and survive reopening."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "config/read": {"config": {"narratorEnabled": True}},
    "runtime/read": {"runtime": {"config": {"narratorEnabled": True}}},
    "config/write": {
        "runtime": {
            "config": {
                "theme": "ansi-dark",
                "voiceModeEnabled": False,
                "narratorEnabled": False,
            }
        }
    },
}
request_methods = {"telemetry/record"}
screen_contains = {
    "rust": ("Voice mode disabled.", "Voice mode: Off", "Narrator (experimental): Off")
}
screen_excludes = {"rust": ("Voice capture is not available",)}
timeline: Timeline = ["/voice\r", "\rj ", "\x1b", "\x12", "/voice\r"]
