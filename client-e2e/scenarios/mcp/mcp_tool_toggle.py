"""`d` on a tool disables just that tool inside the detail view."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

_SOURCES = sample_sources()
_TOGGLED = [
    {**source, "tools": [{**tool, "enabled": False} for tool in source["tools"]]}
    if source["name"] == "filesystem"
    else source
    for source in _SOURCES
]

handshake = mcp_handshake(_SOURCES, toggled=_TOGGLED)

timeline: Timeline = ["/mcp filesystem\r", "d"]
