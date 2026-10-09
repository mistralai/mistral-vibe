"""A steady main input keeps focus semantics and leaves the MCP search caret usable."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake(sample_sources())
handshake["runtime/read"]["runtime"]["config"] = {"cursorBlink": False}

# Replay freezes animation; Rust unit tests exercise both blink phases without sleeps.
timeline: Timeline = [
    "steady input",
    "\x1b[C\x1b[O",
    "\x1b[I\x03/mcp\r/fs",
    "\x1b[C\x1b[O",
    "\x1b[I\x1b[27u\x1b[27u\x1b[27u/reload\r",
]

screen_contains = {"rust": ("Configuration reloaded",)}
