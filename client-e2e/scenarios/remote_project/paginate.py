"""Remote-project paginate behavior over the app-server protocol."""

from __future__ import annotations

from e2e.app_server.remote_project import METHODS, handshake as project_handshake
from e2e.app_server.scenario import Timeline

handshake = project_handshake(more=True)
request_methods = METHODS
timeline: Timeline = ["/remote-project\r", "\x1b[B", "jj", "\r", "\x1b"]
