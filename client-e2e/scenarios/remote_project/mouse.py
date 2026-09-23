"""Remote-project mouse parity."""

from __future__ import annotations

from e2e.app_server.remote_project import METHODS, handshake as project_handshake
from e2e.app_server.scenario import Timeline

request_methods = METHODS
handshake = project_handshake()
timeline: Timeline = [
    "/remote-project\r",
    "\x1b[<0;12;36M\x1b[<0;12;36m",
    "\x1b[<0;22;36M\x1b[<0;22;36m",
    "develop",
    "\x1b",
    "\x1b[<0;10;29M\x1b[<0;10;29m",
]
