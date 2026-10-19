"""Enter on a server that needs OAuth hands over to the auth bottom-app."""

from __future__ import annotations

from e2e.app_server.mcp import (
    SAMPLE_DISCOVERY_ERRORS,
    authenticated_sources,
    handshake as mcp_handshake,
    sample_sources,
)
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(sample_sources(), discovery_errors=SAMPLE_DISCOVERY_ERRORS)
# The login answers with the authenticated catalog, which every later read and
# refresh then serves, so the reopened browser shows the discovered tools.
handshake["mcp/login"] = {"runtime": {"mcp": {"sources": authenticated_sources()}}}

_DOWN = "\x1b[B"

timeline: Timeline = ["/mcp\r", _DOWN + _DOWN, "\r"]
