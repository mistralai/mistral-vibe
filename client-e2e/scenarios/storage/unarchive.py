"""On an archived session `a` reads unarchive, asks first, and unarchives without requiring idle."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import (
    ARCHIVED,
    DOWN,
    REQUEST_METHODS,
    SCOPES,
    flagged,
    usage,
)

handshake = flagged({
    "session/archive": ARCHIVED,
    "storage/usage/read": usage(archived_ids=(SCOPES,)),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
})
request_methods = REQUEST_METHODS

# The second session by size is archived.
timeline: Timeline = [
    "/storage",
    "\r",
    DOWN * 2,
    take_snapshot(),
    "a",
    take_snapshot(),
    "y",
    take_snapshot(),
]

screen_contains = ('Unarchived "Add Vibe scopes to the ALBE OIDC config".',)
