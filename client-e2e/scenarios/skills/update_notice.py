"""With registry skills on, a pinned skill's new version is announced once the session is ready."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, registry_on

handshake = {
    **registry_on(),
    "skills/updates": {
        "updates": [{"name": "release-notes", "currentVersion": 2, "latestVersion": 5}]
    },
}
request_methods = METHODS

timeline: Timeline = []

screen_contains = ("New version available for skill(s)",)
