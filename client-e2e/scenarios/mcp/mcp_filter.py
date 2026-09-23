"""Slash focuses fuzzy search, which survives opening tools and shows empty results."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(sample_sources())
request_methods = {"telemetry/record"}

timeline: Timeline = ["/mcp\r", "/", "GH", "\x1b[B", "\r", "\x7f", "/zz"]

screen_contains = {"rust": ("No matching MCP servers or connectors", "GHzz")}
screen_excludes = {"rust": ("Local MCP Servers", "Available Connectors")}
