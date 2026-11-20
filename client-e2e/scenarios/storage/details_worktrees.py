"""A session claiming two worktrees counts both in its size, and its details list each one."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.storage._fixtures import DOWN, REBASE, REQUEST_METHODS, flagged, usage

_REBASE_WT = "dashboard-aaaaaaaaaaaa/rebase"
_SCOPES_WT = "dashboard-aaaaaaaaaaaa/scopes"


def _claims_two() -> dict:
    answer = usage()
    snapshot = answer["snapshot"]
    for row in snapshot["sessions"]:
        if row["sessionId"] == REBASE:
            row["worktreeIds"] = [_REBASE_WT, _SCOPES_WT]
    for worktree in snapshot["worktrees"]:
        if worktree["id"] == _SCOPES_WT:
            worktree["sessionIds"].append(REBASE)
    return answer


handshake = flagged({
    "storage/usage/read": _claims_two(),
    "storage/usage/refresh": {"started": False, "reason": "fresh"},
})
request_methods = REQUEST_METHODS

# Rebase is the largest row: history 400 MB + 1.7 GB + 1.16 GB.
timeline: Timeline = ["/storage", "\r", DOWN, "\r"]

screen_contains = (
    "3.3 GB  42d ago   Rebase PR 71711",
    "2 worktrees · shared by 2",
    "history 400 MB  ·  worktree 1.7 GB on rebase-oidc",
    "worktree 1.2 GB on albe-oidc-vibe-scopes  ·  shared by 2  ·  copy-on-write",
)
