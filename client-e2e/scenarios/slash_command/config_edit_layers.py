"""Keep scalar input and all five layers above the persistence controls."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.slash_command._config_fixtures import AUTO_COMPACT_HANDSHAKE

handshake = AUTO_COMPACT_HANDSHAKE

capture_startup = False
screen_contains = {"rust": ("240000", "220000", "200000", "180000", "compaction.")}
timeline: Timeline = ["/config\r", "\r", "\t", "\t"]
