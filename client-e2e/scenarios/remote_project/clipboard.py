"""Remote fields copy, cut, select all, and paste without cancelling the form."""

from __future__ import annotations

from e2e.app_server.remote_project import METHODS, handshake as project_handshake
from e2e.app_server.scenario import Timeline

handshake = project_handshake()
request_methods = METHODS
env = {"SSH_TTY": "/dev/test"}
expected_clipboard = "New project"
timeline: Timeline = [
    "/remote-project\r",
    "New project",
    "\r",
    "\x03",
    "\x18",
    "\x16",
    "\t",
    "develop",
    "\x1b[97;6u",
    "\x16",
]
