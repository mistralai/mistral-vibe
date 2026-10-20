"""A model pick the administrator pins is not written and the error says why."""

from __future__ import annotations

import json

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.scenario import Timeline

_fields = json.loads(FIXTURE_PATH.read_text())["handshake"]["config/fields/read"]
_fields["fields"][0]["layerValues"] = [
    {"layer": "admin", "value": "mistral-medium-3.5"},
    {"layer": "default", "value": "mistral-medium-3.5"},
]
assert _fields["fields"][0]["name"] == "active_model"

handshake = {"config/fields/read": _fields}

# Pick the next model, then keep its current thinking level in the chained picker.
timeline: Timeline = ["/model", "\r", "\x1b[B", "\r", "\r"]

screen_contains = (
    "'active_model' is enforced by your",
    "administrator. Contact your admin to change it.",
)
screen_excludes = ("Configuration reloaded",)
