"""Space on a locked skill sends nothing and says where it is managed: /plugins, or the config patterns."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, installed, skill

handshake = {
    "skills/installed": installed([
        skill("pdf", source="plugin", locked=True),
        skill("legacy", enabled=False, locked=True),
    ])
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", " ", "j "]

screen_contains = ("'legacy' is set by a",)
