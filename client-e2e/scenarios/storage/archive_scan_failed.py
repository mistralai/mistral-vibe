"""The measuring scan failing closes the panel with the result and no measured size."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import (
    ARCHIVED,
    DOWN,
    MIRROR,
    REQUEST_METHODS,
    flagged,
    refreshing,
    refused,
    updated,
    usage,
)

_BEFORE = usage()
_FAILED = updated({**_BEFORE, "status": "failed"}, scan=2)

handshake = flagged({
    "session/archive": ARCHIVED,
    "storage/usage/read": _BEFORE,
    # The startup refresh finds the numbers fresh.
    "storage/usage/refresh": [
        {"started": False, "reason": "fresh"},
        {"started": True, "scanId": 1},
        {"started": True, "scanId": 2},
    ],
    f"session/archive#{MIRROR}": refused(MIRROR),
})
# The measuring scan starts, then fails: the server publishes the cached snapshot as `failed`.
on_request = {
    "storage/usage/refresh": {"calls": [[], [], [refreshing(_BEFORE, scan=2), _FAILED]]}
}
request_methods = REQUEST_METHODS | {"telemetry/record"}

# Review largest first: Rebase, Santa, Mirror, Fix flaky. Uncheck the last, then archive.
timeline: Timeline = [
    "/storage",
    "\r",
    "\r",
    DOWN * 3,
    " ",
    take_snapshot(),
    "\r",
    take_snapshot(),
]

screen_contains = (
    "Archived 2 sessions. About 1.8 GB is freed as their worktrees are removed",
    "Kept 1 session, busy (working or open in another app): Mirror ALBE scopes in vortex.",
    "Kept 1 session you unselected: Fix the flaky retry-policy test.",
)
screen_excludes = ("Vibe storage",)
