"""Enter on the suggestion opens the review with every suggested session checked and the estimate."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import REQUEST_METHODS, flagged, usage

handshake = flagged({
    "storage/usage/read": usage(),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
})
request_methods = REQUEST_METHODS

timeline: Timeline = ["/storage", "\r", "\r", take_snapshot()]

screen_contains = (
    "Archive inactive sessions",
    "[x]",
    "Archive 4 sessions →   frees about 1.8 GB  ·  uncommitted work is saved to a git ref",
    "s sort: size",
)
screen_excludes = ("[ ]",)
