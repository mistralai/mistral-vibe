"""Remote-project resize recovery."""

from __future__ import annotations

from e2e.app_server.remote_project import (
    METHODS,
    handshake as project_handshake,
    project,
)
from e2e.app_server.scenario import Timeline, resize

request_methods = METHODS
handshake = project_handshake(saved=False)
handshake["vibeCode/projects/open"]["view"]["state"]["projects"] = [
    project(str(i), f"Project {i:02d} " + "x" * 50) for i in range(30)
]
timeline: Timeline = [
    "/remote-project\r",
    "j" * 25,
    "\x1b[<65;50;25M",
    "\x1b[A",
    resize(30, 80),
    "\x1b",
]

screen_excludes = ("Search projects:", "Project 24")
