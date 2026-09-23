"""Remote-project empty parity."""

from __future__ import annotations

from e2e.app_server.remote_project import METHODS, handshake as project_handshake
from e2e.app_server.scenario import Timeline

request_methods = METHODS
handshake = project_handshake(saved=False, empty=True)
timeline: Timeline = ["/remote-project\r", "\r", "\x1b", "\x1b"]
