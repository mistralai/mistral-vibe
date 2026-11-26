"""A refused change is reported and the pins are re-read anyway, so the list shows what the server holds."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, installed, skill

handshake = {
    "skills/installed": installed([skill("code-review")]),
    "skills/setEnabled": {
        "error": {"code": "conflict", "message": "A turn is already running"}
    },
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", " "]

screen_contains = (
    "Disabling code-review failed: A turn is already running",
    "● code-review",
)
