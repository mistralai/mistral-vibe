"""`/storage` opens on the total, the four categories, the suggestion and every session by size."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import REQUEST_METHODS, flagged, usage

handshake = flagged({
    "storage/usage/read": usage(),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
})
request_methods = REQUEST_METHODS

timeline: Timeline = ["/storage", "\r", take_snapshot()]

screen_contains = (
    "Vibe storage  17.6 GB used",
    "99 GB free of 494 GB",
    "updated 2 minutes ago",
    "Archive 4 sessions inactive for 30+ days  ·  frees about 1.8 GB →",
    "open · shared by 2",
    "Enter review",
)
