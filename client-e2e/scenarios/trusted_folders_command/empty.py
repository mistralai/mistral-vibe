"""/trusted-folders opens on an empty trust store, so a first folder can be added."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.trusted_folders import METHODS, folders

handshake = {"workspace/trust/folders/list": folders([], [])}
request_methods = METHODS

timeline: Timeline = ["/trusted-folders\r"]

screen_contains = ("Trusted folders · 0 stored", "a add")
