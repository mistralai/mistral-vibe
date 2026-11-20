"""The cleanup suggestion offers leftover worktrees and unknown folders; its review asks y/n, then removes the checked ones in one request."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import (
    DOWN,
    REQUEST_METHODS,
    SCRATCH_DIR,
    SPIKE_WT,
    TARGET_DIR,
    cleaned,
    flagged,
    updated,
    usage,
)

_BEFORE = usage(suggested=[], leftovers=True)
_REMOVED = [SPIKE_WT, TARGET_DIR, SCRATCH_DIR]

handshake = flagged({
    "storage/usage/read": _BEFORE,
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
    "storage/leftovers/clean": {"removed": _REMOVED, "kept": []},
})
# The server refreshes after a removal; its scan no longer finds what went.
on_request = {"storage/leftovers/clean": [updated(cleaned(_BEFORE, _REMOVED))]}
request_methods = REQUEST_METHODS

# Open the cleanup review, uncheck the second worktree (Retry); Enter asks, `n` goes back, Enter asks
# again, a second Enter does nothing (only `y` removes), and `y` cleans up.
timeline: Timeline = [
    "/storage",
    "\r",
    take_snapshot(),
    "\r",
    take_snapshot(),
    DOWN,
    " ",
    "\r",
    take_snapshot(),
    "n",
    "\r",
    "\r",
    "y",
    take_snapshot(),
]

screen_contains = (
    "Cleaned up 3 items. Freed about 2.7 GB.",
    "Clean up 1 leftover worktree  ·  frees about 120 MB →",
)
screen_excludes = ("Kept ",)
