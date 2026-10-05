"""Opening reads without re-discovering; `r` waits for init, then refreshes connectors and servers."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(sample_sources())
request_methods = {
    "connector_catalog/refresh",
    "mcp_catalog/read",
    "mcp_catalog/refresh",
}

timeline: Timeline = ["/mcp\r", "r"]
