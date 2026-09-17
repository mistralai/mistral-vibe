"""`/mcp` without configured sources mounts a message instead of the browser."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake([])

timeline: Timeline = ["/mcp\r"]
