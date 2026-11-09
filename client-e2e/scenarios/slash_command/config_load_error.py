"""A `/config` field read that fails shows the server's reason instead of an empty screen."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "config/fields/read": {
        "error": {"code": "internal_error", "message": "Config could not be read"}
    }
}

capture_startup = False
screen_contains = ("Failed to load settings: Config could not be read",)
timeline: Timeline = ["/config\r"]
