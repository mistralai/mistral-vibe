"""A session the server finds open elsewhere is kept by the bulk delete, and the result row says why."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.storage._fixtures import (
    FLAKY,
    REQUEST_METHODS,
    SANTA,
    flagged,
    sessions_deleted,
    usage,
)

handshake = flagged({
    "storage/usage/read": usage(
        archived_ids=(SANTA, FLAKY), suggested=[], delete_suggested=[SANTA, FLAKY]
    ),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
    "storage/sessions/delete": sessions_deleted(
        [SANTA],
        kept=[{"id": FLAKY, "reason": "in_use"}],
        worktrees=["santa-bbbbbbbbbbbb/demo"],
        freed=246_000_000,
    ),
})
request_methods = REQUEST_METHODS

timeline: Timeline = ["/storage", "\r", "\r", "\r", "y"]

screen_contains = (
    "Deleted 1 session. Freed about 246 MB.",
    "Kept 1 session: open in another app or working (Fix the flaky retry-policy test).",
)
