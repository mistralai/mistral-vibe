"""Remote projects rank the saved link first and preserve selection while filtering."""

from __future__ import annotations

from e2e.app_server.remote_project import METHODS, handshake as project_handshake
from e2e.app_server.scenario import Timeline

handshake = project_handshake()
request_methods = METHODS
timeline: Timeline = ["/remote-project\r", "Another", "\x1b[B", "/", "\x05\x15", "\x1b"]
