"""A refresh that leaves the viewed server awaiting OAuth hands it to its login."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources, server
from e2e.app_server.scenario import Timeline


def _expired() -> list[dict[str, object]]:
    """The catalog once `filesystem` lost its credentials."""
    return [
        server("filesystem", transport="http", status="needs_auth")
        if source["name"] == "filesystem"
        else source
        for source in sample_sources()
    ]


handshake = mcp_handshake(sample_sources())
handshake["mcp/refresh"] = {"runtime": {"mcp": {"sources": _expired()}}}
request_methods = {"mcp_catalog/login", "mcp_catalog/refresh"}

timeline: Timeline = ["/mcp filesystem\r", "r"]
