"""Enter on a server lists its tools, Backspace returns to the source list."""

from __future__ import annotations

from e2e.app_server.mcp import (
    SAMPLE_DISCOVERY_ERRORS,
    handshake as mcp_handshake,
    sample_sources,
)
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(sample_sources(), discovery_errors=SAMPLE_DISCOVERY_ERRORS)

timeline: Timeline = ["/mcp\r", "\r", "\x7f"]
