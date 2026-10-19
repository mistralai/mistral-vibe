"""A `/config` edit with layer write failures shows them in a toast and reloads the list."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._config_fixtures import AUTO_COMPACT_HANDSHAKE

handshake = {
    **AUTO_COMPACT_HANDSHAKE,
    "config/write": {
        "rejected": False,
        "failures": ["config.toml changed on disk; reload and retry"],
    },
}

capture_startup = False
screen_contains = (
    "Failed to save auto_compact_threshold (temporary):",
    "config.toml changed on disk; reload and retry",
)
timeline: Timeline = ["/config\r", "\r", "\x7f" * 6 + "5000", "\r"]
