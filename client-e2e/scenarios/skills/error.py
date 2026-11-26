"""A failing skills/installed reports the server's message instead of opening the browser."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS

handshake = {
    "skills/installed": {
        "error": {"code": "internal_error", "message": "Skill discovery failed"}
    }
}
request_methods = METHODS

timeline: Timeline = ["/skills\r"]

screen_contains = ("Failed to read skills: Skill discovery failed",)
screen_excludes = ("Search skills",)
