"""Remote-project resize recovery, including a known Textual reflow gap."""

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
    "\t",
    "j" * 25,
    "\x1b[<65;50;25M",
    "\x1b[A",
    resize(30, 80),
    "\x1b",
]

skip_terminal_parity = (
    "Textual retains the old picker dimensions after PTY shrink; Rust reflows."
)
screen_excludes = {"rust": ("Search projects:", "Project 24")}
