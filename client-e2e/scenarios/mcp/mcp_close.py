"""Esc closes the browser and mounts the closed message."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline, take_snapshot

handshake = mcp_handshake(sample_sources())

_ESCAPE = "\x1b[27u"

timeline: Timeline = ["/mcp\r", _ESCAPE, take_snapshot()]
