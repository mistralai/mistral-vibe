"""With `deleteBytes: null` from an older cache, the row delete and the partial review leave the size out."""

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
        archived_ids=(SANTA, FLAKY),
        suggested=[],
        delete_suggested=[SANTA, FLAKY],
        unknown_delete_bytes=True,
    ),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
})
request_methods = REQUEST_METHODS

# Uncheck Fix flaky in the delete review: only Santa, whose size is unknown, stays selected; Enter asks.
timeline: Timeline = ["/storage", "\r", "\r", DOWN, " ", "\r"]

screen_contains = (
    "Delete 1 archived session? Their history is deleted for good.",
    "Delete 1 session →  ·  uncommitted work is saved to a git ref",
)
screen_excludes = ("Frees", "frees about 0 B", "frees at least 0 B")
