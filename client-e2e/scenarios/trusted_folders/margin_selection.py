"""Side-margin drags select text in both directions without requiring overflow."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.trusted_folders.gate_padding_press import handshake as handshake

env = {"SSH_TTY": "/dev/pts/0"}
expected_clipboard = "• .vibe/"

timeline: Timeline = [
    "\x1b[<0;30;15M\x1b[<32;80;15M\x1b[<0;80;15m",
    "\x1b[<0;91;16M\x1b[<32;40;16M\x1b[<0;40;16m",
]
