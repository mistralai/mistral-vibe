"""`a` on a session open in another app is refused locally; the panel stays open."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import DOWN, REQUEST_METHODS, flagged, usage

handshake = flagged({
    "storage/usage/read": usage(),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
})
request_methods = REQUEST_METHODS

# The third session by size is open elsewhere.
timeline: Timeline = ["/storage", "\r", DOWN * 3, "a", take_snapshot()]

screen_contains = (
    "This session is open in another app. Close it there first.",
    "Vibe storage",
)
