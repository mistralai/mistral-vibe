"""Cached numbers show `refreshing…` while a scan runs; `r` starts another, whose result clears it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import (
    REQUEST_METHODS,
    flagged,
    refreshing,
    updated,
    usage,
)

_CACHED = usage()

handshake = flagged({
    "storage/usage/read": _CACHED,
    # The startup refresh finds the numbers fresh; the panel's starts scan 1, `r` scan 2.
    "storage/usage/refresh": [
        {"started": False, "reason": "fresh"},
        {"started": True, "scanId": 1},
        {"started": True, "scanId": 2},
    ],
})
on_request = {
    "storage/usage/refresh": {
        "calls": [
            [],
            [refreshing(_CACHED, scan=1)],
            [
                updated(_CACHED, scan=1),
                refreshing(_CACHED, scan=2),
                updated(_CACHED, scan=2),
            ],
        ]
    }
}
request_methods = REQUEST_METHODS

timeline: Timeline = ["/storage", "\r", take_snapshot(), "r", take_snapshot()]

screen_contains = ("updated 2 minutes ago",)
screen_excludes = ("refreshing…",)
