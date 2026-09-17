"""Down-arrow skips group headers and the separator between the two groups."""

from __future__ import annotations

from e2e.app_server.mcp import (
    SAMPLE_DISCOVERY_ERRORS,
    handshake as mcp_handshake,
    sample_sources,
)
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(sample_sources(), discovery_errors=SAMPLE_DISCOVERY_ERRORS)

_DOWN = "\x1b[B"

timeline: Timeline = ["/mcp\r", _DOWN, _DOWN, _DOWN]
