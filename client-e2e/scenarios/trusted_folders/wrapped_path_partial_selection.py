"""A partial reverse drag across wrapped path rows copies only source characters."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize
from scenarios.trusted_folders.wrapped_path_selection import handshake as handshake

env = {"SSH_TTY": "/dev/pts/0"}
expected_clipboard = "/projects/abcdefghijklmnopqrstuvwxyz0123456789/ano"

timeline: Timeline = [
    resize(40, 60),
    "\x1b[<0;17;23M\x1b[<32;19;24M\x1b[<0;19;24m",
    "\x1b[<0;19;24M\x1b[<32;17;23M\x1b[<0;17;23m",
]
