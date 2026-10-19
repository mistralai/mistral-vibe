"""A `/config` edit the server rejects shows an error toast naming the field and layer."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._config_fixtures import AUTO_COMPACT_HANDSHAKE

handshake = {
    **AUTO_COMPACT_HANDSHAKE,
    "config/write": {"rejected": True, "failures": []},
}

capture_startup = False
screen_contains = (
    "Failed to save auto_compact_threshold (temporary):",
    "Invalid configuration edit",
)
timeline: Timeline = ["/config\r", "\r", "\x7f" * 6 + "5000", "\r"]
