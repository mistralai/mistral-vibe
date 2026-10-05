"""Both voice settings toggle in place and persist together only on Escape."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "config/read": {"config": {"voiceModeEnabled": False}},
    "runtime/read": {"runtime": {"config": {"voiceModeEnabled": False}}},
    "config/write": {
        "runtime": {
            "config": {
                "theme": "ansi-dark",
                "voiceModeEnabled": True,
                "narratorEnabled": True,
            }
        }
    },
}
request_methods = {"telemetry/record"}
screen_contains = {"rust": ("Voice mode enabled.",)}
timeline: Timeline = ["/voice\r", " ", "j\r", "\x1b"]
