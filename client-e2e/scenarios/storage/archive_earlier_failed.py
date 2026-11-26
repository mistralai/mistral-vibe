"""An earlier scan failing keeps the wait for the measuring scan, whose result is recorded."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import (
    ARCHIVED,
    DOWN,
    MIRROR,
    REBASE,
    REQUEST_METHODS,
    SANTA,
    archived,
    flagged,
    refreshing,
    refused,
    updated,
    usage,
)

_BEFORE = usage()
_AFTER = archived(_BEFORE, [REBASE, SANTA], freed=1_761_000_000)
_FAILED = updated({**_BEFORE, "status": "failed"}, scan=1)

handshake = flagged({
    "session/archive": ARCHIVED,
    "storage/usage/read": _BEFORE,
    # The open refresh starts a scan; the measuring one queues behind it.
    # The startup refresh finds the numbers fresh.
    "storage/usage/refresh": [
        {"started": False, "reason": "fresh"},
        {"started": True, "scanId": 1},
        {"started": False, "reason": "already_running", "scanId": 2},
    ],
    f"session/archive#{MIRROR}": refused(MIRROR),
})
# An earlier scan fails, then the measuring scan starts and ends with the archived numbers.
on_request = {
    "storage/usage/refresh": {
        "calls": [
            [],
            [],
            [_FAILED, refreshing(_BEFORE, scan=2), updated(_AFTER, scan=2)],
        ]
    }
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
