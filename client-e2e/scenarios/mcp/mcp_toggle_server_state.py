"""The server's answer repaints toggled rows; `e` never paints a source enabled early."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

# No `toggled` catalog: the server answers every toggle with the unchanged state.
handshake = mcp_handshake(sample_sources())

_DOWN = "\x1b[B"

timeline: Timeline = ["/mcp\r", "d", _DOWN * 4, "e"]
