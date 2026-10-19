"""A `/config` edit failing with an RPC error shows the server's own message in the toast."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._config_fixtures import AUTO_COMPACT_HANDSHAKE

handshake = {
    **AUTO_COMPACT_HANDSHAKE,
    "config/write": {
        "error": {"code": "conflict", "message": "Session is busy; retry when idle"}
    },
}

capture_startup = False
screen_contains = (
    "Failed to save auto_compact_threshold (temporary):",
    "Session is busy; retry when idle",
)
screen_excludes = ("config/write failed",)
timeline: Timeline = ["/config\r", "\r", "\x7f" * 6 + "5000", "\r"]
