"""`/mcp <unknown>` reports the known source names."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(sample_sources())

timeline: Timeline = ["/mcp nope\r"]
