"""Without a saved link the picker opens; picking a project continues the teleport."""

from __future__ import annotations

from e2e.app_server.remote_project import project
from e2e.app_server.scenario import Timeline
from e2e.app_server.teleport import (
    METHODS,
    START,
    complete,
    handshake as teleport_handshake,
)

handshake = teleport_handshake(resolved=None, saved=False)
handshake["vibeCode/projects/select"]["project"] = project("exact", "Another project")
request_methods = METHODS
on_request = {START: complete()}
timeline: Timeline = ["&ship it\r", "\r"]
