"""When the server suggests deleting old archived sessions none of which this client can delete, the hint says they are open."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.storage._fixtures import REQUEST_METHODS, TRACK, flagged, usage

handshake = flagged({
    "storage/usage/read": usage(
        archived_ids=(TRACK,), suggested=[], delete_suggested=[TRACK]
    ),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
})
request_methods = REQUEST_METHODS

# Track is open in another app: it can't be deleted here, so the hint replaces the delete row.
timeline: Timeline = ["/storage", "\r"]

screen_contains = (
    "1 archived session inactive for 30+ days is open, so it can't be deleted now.",
)
screen_excludes = ("Delete 1 archived session",)
