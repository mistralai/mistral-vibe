"""Remote-project create back behavior over the app-server protocol."""

from __future__ import annotations

from e2e.app_server.remote_project import METHODS, handshake as project_handshake
from e2e.app_server.scenario import Timeline

handshake = project_handshake()
request_methods = METHODS
timeline: Timeline = [
    "/remote-project\r",
    "New project",
    "\r",
    "\x7f",
    "\r",
    "\x1b",
    "\x03",
]
