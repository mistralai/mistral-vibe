"""Remote-project dark parity."""

from __future__ import annotations

from e2e.app_server.remote_project import METHODS, handshake as project_handshake
from e2e.app_server.scenario import Timeline

request_methods = METHODS
handshake = project_handshake()
env = {"VIBE_THEME": "atom-one-dark"}
handshake["runtime/read"] = {"runtime": {"config": {"theme": "atom-one-dark"}}}
handshake["config/read"] = {"config": {"theme": "atom-one-dark"}}
timeline: Timeline = [
    "/remote-project\r",
    "\x1b[B",
    "/",
    "New project",
    "\r",
    "\t",
    "\x1b",
]
