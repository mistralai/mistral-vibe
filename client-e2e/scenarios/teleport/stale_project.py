"""A saved project that no longer exists is unlinked and the picker reopens."""

from __future__ import annotations

from copy import deepcopy

from e2e.app_server.scenario import Timeline
from e2e.app_server.teleport import (
    METHODS,
    RECOVER,
    START,
    failed,
    handshake as teleport_handshake,
)

handshake = teleport_handshake()
_view = deepcopy(handshake["vibeCode/projects/open"]["view"])
_view["context"]["savedLink"] = None
handshake[RECOVER] = {"recovered": True, "view": _view}
request_methods = METHODS
on_request = {
    START: [failed("Saved Vibe Code project not found", "saved_project_stale")]
}
timeline: Timeline = ["/teleport\r"]
