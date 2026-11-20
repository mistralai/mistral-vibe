"""In the delete review, Space and `a` change the selection and the estimate; `n` on the question goes back without deleting."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.storage._fixtures import (
    DOWN,
    FLAKY,
    REQUEST_METHODS,
    SANTA,
    flagged,
    usage,
)

handshake = flagged({
    "storage/usage/read": usage(
        archived_ids=(SANTA, FLAKY), suggested=[], delete_suggested=[SANTA, FLAKY]
    ),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
})
request_methods = REQUEST_METHODS

# Uncheck Santa, check all again, uncheck Fix flaky; Enter asks, `n` answers no.
timeline: Timeline = ["/storage", "\r", "\r", " ", "a", DOWN, " ", "\r", "n"]

screen_contains = (
    "Delete archived sessions",
    "Deleting removes each session's history for good.",
    "[x] ",
    "[ ] ",
    "Delete 1 session →   frees about 246 MB",
    "Enter delete",
)
screen_excludes = ("Their history is deleted for good.", "Deleted ")
