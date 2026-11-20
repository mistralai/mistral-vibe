"""Deleting a session whose worktree another session shares frees only its history, as the row's `deleteBytes` says."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import (
    DOWN,
    REQUEST_METHODS,
    REVIEW,
    flagged,
    sessions_deleted,
    usage,
)

handshake = flagged({
    "storage/usage/read": usage(),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
    "storage/sessions/delete": sessions_deleted([REVIEW], freed=1_000_000),
})
request_methods = REQUEST_METHODS

# Rows by size: suggestion, Rebase, Scopes, Track (open), Review; Review shares Track's worktree.
timeline: Timeline = ["/storage", "\r", DOWN * 4, "d", take_snapshot(), "y"]

screen_contains = (
    'Deleted "Review the disk usage prototype". Freed about 1 MB.',
    "Track disk usage per session",
)
screen_excludes = ("882 MB  today     Review",)
