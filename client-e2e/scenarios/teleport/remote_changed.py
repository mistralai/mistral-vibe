"""A saved link for another remote is cleared and explained before the picker."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.teleport import METHODS, handshake as teleport_handshake

handshake = teleport_handshake(resolved=None, saved=False)
handshake["vibeCode/projects/open"]["view"]["savedProjectLinkCleared"] = True
request_methods = METHODS
timeline: Timeline = ["/teleport\r", "\x1b"]
