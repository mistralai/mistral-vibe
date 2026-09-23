"""Remote-project page and boundary key parity."""

from __future__ import annotations

from e2e.app_server.remote_project import (
    METHODS,
    handshake as project_handshake,
    project,
)
from e2e.app_server.scenario import Timeline

request_methods = METHODS
handshake = project_handshake(saved=False)
handshake["vibeCode/projects/open"]["view"]["state"]["projects"] = [
    project(str(i), f"Project {i:02d} " + "x" * 50) for i in range(30)
]
timeline: Timeline = [
    "/remote-project\r",
    "\t",
    "\x1b[6~",
    "\x1b[5~",
    "\x1b[5~",
    "j",
    "\x1b[F",
    "j",
    "k",
    "\x1b[H",
    "k",
    "\x1b",
]
