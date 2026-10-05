"""A narrator-only edit leaves voice unchanged and emits no voice-toggle event."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "config/write": {
        "runtime": {"config": {"theme": "ansi-dark", "narratorEnabled": True}}
    }
}
request_methods = {"telemetry/record"}
screen_excludes = {"rust": ("Voice mode enabled.", "Voice mode disabled.")}
timeline: Timeline = ["/voice\r", "j ", "\x1b"]
