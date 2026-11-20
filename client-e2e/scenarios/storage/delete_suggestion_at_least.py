"""A delete suggestion whose freed worktree is not measured yet reads "at least", on its row and in its review's question."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.storage._fixtures import FLAKY, REQUEST_METHODS, SANTA, flagged, usage

_USAGE = usage(
    archived_ids=(SANTA, FLAKY), suggested=[], delete_suggested=[SANTA, FLAKY]
)
_USAGE["snapshot"]["deleteSuggestion"]["reclaimableAtLeast"] = True

handshake = flagged({
    "storage/usage/read": _USAGE,
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
})
request_methods = REQUEST_METHODS

# Every suggested session stays checked, so the question shows the server's total and its floor.
timeline: Timeline = ["/storage", "\r", "\r", "\r"]

screen_contains = (
    "Delete 2 archived sessions? Their history is deleted for good. Frees at least 270 MB.",
    "frees at least 270 MB",
)
