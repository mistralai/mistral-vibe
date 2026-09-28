"""`d` disables the highlighted server and the server answers with the new state."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

_SOURCES = sample_sources()
_TOGGLED = [
    {**source, "status": "disabled"} if source["name"] == "filesystem" else source
    for source in _SOURCES
]

handshake = mcp_handshake(_SOURCES, toggled=_TOGGLED)

timeline: Timeline = ["/mcp\r", "d"]
