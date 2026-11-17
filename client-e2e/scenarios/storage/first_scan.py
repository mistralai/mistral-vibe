"""Before the first scan `/storage` says it is measuring and that it can take a few minutes."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import REQUEST_METHODS, flagged, usage

_NO_SNAPSHOT = {**usage(), "status": "refreshing", "snapshot": None}

handshake = flagged({
    "storage/usage/read": _NO_SNAPSHOT,
    "storage/usage/refresh": {"started": True, "scanId": 1},
})
request_methods = REQUEST_METHODS

timeline: Timeline = ["/storage", "\r", take_snapshot()]

screen_contains = (
    "99 GB free of 494 GB",
    "Measuring Vibe storage…",
    "This can take a few minutes with many sessions and worktrees.",
)
