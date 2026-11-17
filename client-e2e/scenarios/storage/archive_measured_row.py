"""After a bulk archive, the result row says the space the scan measured, not the review's estimate; the scan's start does not end the wait.

The measure counts the archived worktrees the scan no longer finds: here Santa's is still there.
"""

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
# The scan finds Rebase's worktree (1.7 GB) gone but Santa's (61 MB) still there: it measures
# less than the review estimated (about 1.8 GB).
_AFTER = archived(_BEFORE, [REBASE], freed=1_700_000_000)
for _row in _AFTER["snapshot"]["sessions"]:
    if _row["sessionId"] == SANTA:
        _row["archived"] = True

handshake = flagged({
    "session/archive": ARCHIVED,
    "storage/usage/read": _BEFORE,
    "storage/usage/refresh": [
        {"started": False, "reason": "fresh"},
        {"started": False, "reason": "fresh"},
        {"started": True, "scanId": 3},
    ],
    f"session/archive#{MIRROR}": refused(MIRROR),
})
# The measuring scan starts (still with the pre-archive numbers), then ends with what it measured.
on_request = {
    "storage/usage/refresh": {
        "calls": [[], [], [refreshing(_BEFORE, scan=3), updated(_AFTER, scan=3)]]
    }
}
request_methods = REQUEST_METHODS | {"telemetry/record"}

# Review largest first: Rebase, Santa, Mirror, Fix flaky. Uncheck the last, then archive.
timeline: Timeline = ["/storage", "\r", "\r", DOWN * 3, " ", "\r", take_snapshot()]

screen_contains = (
    "Archived 2 sessions. About 1.7 GB is freed as their worktrees are removed",
)
