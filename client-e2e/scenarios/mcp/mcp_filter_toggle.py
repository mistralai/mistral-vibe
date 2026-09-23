"""Toggling a filtered source targets its identity and preserves the search after refresh."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

_SOURCES = sample_sources()
_TOGGLED = [
    {**source, "status": "disabled"} if source["name"] == "filesystem" else source
    for source in _SOURCES
]

handshake = mcp_handshake(_SOURCES, toggled=_TOGGLED)
request_methods = {"telemetry/record", "mcp/toggle"}

timeline: Timeline = ["/mcp\r", "/fsy", "\x1b[B", "d", "\r", "\x7f"]

screen_contains = {"rust": ("fsy", "filesystem", "disabled")}
screen_excludes = {"rust": ("Available Connectors", "github", "broken")}
