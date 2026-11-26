"""A failing skills/catalog leaves the installed list usable and says the shared skills could not load."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, installed, registry_on, skill

handshake = {
    **registry_on(),
    "skills/installed": installed([skill("code-review")]),
    "skills/catalog": {"error": {"code": "internal_error", "message": "Registry down"}},
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\t"]

screen_contains = ("Could not load the shared skills catalog",)
