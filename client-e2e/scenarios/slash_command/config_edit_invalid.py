"""Keep invalid scalar drafts and their validation error visible in the editor."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._config_fixtures import AUTO_COMPACT_HANDSHAKE

handshake = AUTO_COMPACT_HANDSHAKE

capture_startup = False
screen_contains = {"rust": ("256000x", "Expected an integer")}
timeline: Timeline = ["/config\r", "\r", "x\r", "\x1b[D"]
