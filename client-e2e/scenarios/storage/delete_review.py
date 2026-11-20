"""The delete review asks y/n with the server's total, then deletes the checked sessions in one `storage/sessions/delete`."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import (
    DOWN,
    FLAKY,
    MIRROR,
    REBASE,
    REQUEST_METHODS,
    SANTA,
    flagged,
    sessions_deleted,
    usage,
)

handshake = flagged({
    "storage/usage/read": usage(
        archived_ids=(SANTA, FLAKY),
        suggested=[REBASE, MIRROR],
        delete_suggested=[SANTA, FLAKY],
    ),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
    "storage/sessions/delete": sessions_deleted(
        [SANTA, FLAKY], worktrees=["santa-bbbbbbbbbbbb/demo"], freed=270_000_000
    ),
})
request_methods = REQUEST_METHODS

# Both suggestions and the archived-sessions hint, then the delete review, its question, and the delete.
timeline: Timeline = [
    "/storage",
    "\r",
    take_snapshot(),
    DOWN,
    "\r",
    take_snapshot(),
    "\r",
    take_snapshot(),
    "y",
    take_snapshot(),
]

screen_contains = ("Deleted 2 sessions. Freed about 270 MB.", "Vibe storage")
screen_excludes = ("Santa voice demo", "Delete 2 archived sessions")
