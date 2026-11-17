"""Deleting a session whose snapshot peer was deleted since removes the worktree once no saved session is left in it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.storage._fixtures import (
    DOWN,
    NO_SESSIONS_LEFT,
    REQUEST_METHODS,
    flagged,
    usage,
)

handshake = flagged({
    "storage/usage/read": usage(),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
    "session/list": NO_SESSIONS_LEFT,
    "workspace/git/worktrees/remove": {
        "outcome": "removed",
        "branchDeleted": True,
        "reasons": [],
    },
})
request_methods = REQUEST_METHODS

# Review shares Track's worktree in the snapshot, but no saved session is left in it now.
timeline: Timeline = ["/storage", "\r", DOWN * 4, "d", "y"]

screen_contains = ("Its worktree was removed.",)
screen_excludes = ("Its worktree stays",)
