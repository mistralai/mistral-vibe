"""A failed catalog check (offline, older server) clears the check line without a message."""

from __future__ import annotations

from e2e.app_server.events import SESSION_ID
from e2e.app_server.scenario import Timeline
from scenarios.model._locked import LARGE, handshake

handshake = handshake(**{
    f"config/read#{SESSION_ID}": {
        "error": {"code": "invalid_params", "message": "refreshModelCatalog: extra"}
    }
})
request_methods = {"config/read"}
screen_contains = ("Mistral Medium 3.5",)
screen_excludes = (LARGE, "Checking for new models", "refreshModelCatalog", "Failed")

timeline: Timeline = ["/model", "\r"]
