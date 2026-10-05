"""An ineligible teleport shows the server's reason and starts nothing."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.teleport import METHODS, handshake as teleport_handshake

handshake = teleport_handshake()
handshake.pop("vibeCode/projects/open")
request_methods = METHODS
timeline: Timeline = ["/teleport\r"]
