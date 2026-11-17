"""A single archive the server refuses as busy shows a warning and leaves the session listed."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import (
    ARCHIVED,
    DOWN,
    REBASE,
    REQUEST_METHODS,
    flagged,
    refused,
    usage,
)

handshake = flagged({
    "session/archive": ARCHIVED,
    "storage/usage/read": usage(),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
    f"session/archive#{REBASE}": refused(REBASE),
})
request_methods = REQUEST_METHODS

timeline: Timeline = [
    "/storage",
    "\r",
    DOWN,
    "a",
    take_snapshot(),
    "y",
    take_snapshot(),
]

screen_contains = ("This session is busy (working or open in another app).",)
screen_excludes = ("✓ Archived",)
