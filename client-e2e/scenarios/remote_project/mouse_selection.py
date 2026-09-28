"""Remote-project field selection follows a captured mouse drag."""

from __future__ import annotations

from e2e.app_server.remote_project import METHODS, handshake as project_handshake
from e2e.app_server.scenario import Timeline

handshake = project_handshake()
request_methods = METHODS
expected_clipboard = "New"
capture_steps = {4}
timeline: Timeline = [
    "/remote-project\r",
    "New project",
    "\r",
    "\x1b[<0;19;34M\x1b[<32;22;34M\x1b[<0;22;34m",
    "\x03",
]
