"""The reviewed sessions archive one at a time; one refused, one unselected; the result lands in the conversation."""

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
    refused,
    updated,
    usage,
)

_BEFORE = usage()
_AFTER = archived(_BEFORE, [REBASE, SANTA], freed=1_761_000_000)

handshake = flagged({
    "session/archive": ARCHIVED,
    "storage/usage/read": _BEFORE,
    "storage/usage/refresh": {"started": True},
    f"session/archive#{MIRROR}": refused(MIRROR),
})
# The startup refresh reports nothing; the open refresh reports the snapshot it has; the post-archive one the measured result.
on_request = {
    "storage/usage/refresh": {"calls": [[], [updated(_BEFORE)], [updated(_AFTER)]]}
}
request_methods = REQUEST_METHODS

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
