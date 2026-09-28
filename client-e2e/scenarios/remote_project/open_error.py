"""Remote-project open error behavior over the app-server protocol."""

from __future__ import annotations

from e2e.app_server.remote_project import METHODS, handshake as project_handshake
from e2e.app_server.scenario import Timeline

handshake = project_handshake()
request_methods = METHODS
handshake.pop("vibeCode/projects/open")
timeline: Timeline = ["/remote-project\r"]
