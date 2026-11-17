"""Shared `/storage` fixtures: the design doc's example snapshot, pinned to the replay clock."""

from __future__ import annotations

import copy
from typing import Any

from e2e.app_server.config import REPLAY_NOW_MS

ESCAPE = "\x1b[27u"
DOWN = "\x1b[B"
UP = "\x1b[A"
CURRENT = "00000000-0000-4000-8000-000000000001"
REQUEST_METHODS = {
    "storage/usage/read",
    "storage/usage/refresh",
    "session/archive",
    "session/delete",
    "workspace/git/worktrees/remove",
    "session/list",
}

# `session/list` with no saved session left in the deleted session's worktree.
NO_SESSIONS_LEFT = {"items": [], "nextCursor": None, "previousCursor": None}

# The `vibe_storage_usage` flag, as the app server projects it into the runtime config.
FLAG_ON = {
    "runtime/read": {"runtime": {"config": {"experimentalEnableStorageUsage": True}}}
}

_DAY = 86_400_000
_GB = 1_000_000_000
_MB = 1_000_000


def _sid(n: int) -> str:
    return f"5e550000-0000-4000-8000-0000000000{n:02d}"


REBASE, SCOPES, TRACK, REVIEW, SANTA, MIRROR, FLAKY, DRAFT = (
    _sid(n) for n in range(1, 9)
)
SUGGESTED = [REBASE, SANTA, MIRROR, FLAKY]


def _session(
    sid: str,
    title: str,
    days: int,
    history: int,
    worktree: str | None,
    cwd: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "sessionId": sid,
        "title": title,
        "cwd": cwd,
        "archived": False,
        "open": False,
        "lastActivityAt": REPLAY_NOW_MS - days * _DAY - 3_600_000,
        "historyBytes": history,
        "worktreeId": worktree,
        **extra,
    }


def _worktree(
    wid: str,
    repo: str,
    branch: str,
    size: int | None,
    sessions: list[str],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": wid,
        "path": f"/home/user/.vibe/worktrees/{wid}",
        "branch": branch,
        "repoName": repo,
        "bytes": size,
        "measuredAt": REPLAY_NOW_MS - 120_000,
        "sessionIds": sessions,
        "leftover": False,
        "managed": True,
        "isCopyOnWrite": False,
        **extra,
    }


_SESSIONS = [
    _session(
        REBASE,
        "Rebase PR 71711 on the OIDC session work",
        42,
        400 * _MB,
        "dashboard-aaaaaaaaaaaa/rebase",
        "/work/dashboard",
    ),
    _session(
        SCOPES,
        "Add Vibe scopes to the ALBE OIDC config",
        3,
        236 * _MB,
        "dashboard-aaaaaaaaaaaa/scopes",
        "/work/dashboard",
    ),
    _session(
        TRACK,
        "Track disk usage per session",
        0,
        7 * _MB,
        "dashboard-aaaaaaaaaaaa/disk",
        "/work/dashboard",
        open=True,
    ),
    _session(
        REVIEW,
        "Review the disk usage prototype",
        0,
        _MB,
        "dashboard-aaaaaaaaaaaa/disk",
        "/work/dashboard",
    ),
    _session(
        SANTA,
        "Santa voice demo",
        66,
        185 * _MB,
        "santa-bbbbbbbbbbbb/demo",
        "/work/santa-voice-demo",
    ),
    _session(
        MIRROR,
        "Mirror ALBE scopes in vortex",
        51,
        12 * _MB,
        "vortex-cccccccccccc/albe",
        "/work/vortex",
    ),
    _session(
        FLAKY, "Fix the flaky retry-policy test", 61, 24 * _MB, None, "/work/dashboard"
    ),
    _session(DRAFT, "Draft the 3.4 changelog", 7, 3 * _MB, None, "/work/mistral-vibe"),
]

_WORKTREES = [
    _worktree(
        "dashboard-aaaaaaaaaaaa/rebase",
        "dashboard",
        "rebase-oidc",
        1_700 * _MB,
        [REBASE],
    ),
    _worktree(
        "dashboard-aaaaaaaaaaaa/scopes",
        "dashboard",
        "albe-oidc-vibe-scopes",
        1_164 * _MB,
        [SCOPES],
        isCopyOnWrite=True,
    ),
    _worktree(
        "dashboard-aaaaaaaaaaaa/disk",
        "dashboard",
        "disk-usage",
        881 * _MB,
        [TRACK, REVIEW],
    ),
    _worktree("santa-bbbbbbbbbbbb/demo", "santa-voice-demo", "main", 61 * _MB, [SANTA]),
    _worktree("vortex-cccccccccccc/albe", "vortex", "albe-scopes", 61 * _MB, [MIRROR]),
]


def flagged(handshake: dict[str, Any]) -> dict[str, Any]:
    """`handshake` with the storage flag on."""
    return {**FLAG_ON, **handshake}


def usage(
    *,
    low: bool = False,
    archived_ids: tuple[str, ...] = (),
    sessions: list[dict[str, Any]] | None = None,
    suggested: list[str] | None = None,
) -> dict[str, Any]:
    """A `storage/usage/read` answer; `low` makes the disk almost full, `archived_ids` archives rows."""
    rows = copy.deepcopy(_SESSIONS if sessions is None else sessions)
    for row in rows:
        row["archived"] = row["sessionId"] in archived_ids
    ids = SUGGESTED if suggested is None else suggested
    reclaim = sum(
        w["bytes"]
        for w in _WORKTREES
        if w["sessionIds"] and all(s in ids for s in w["sessionIds"])
    )
    return {
        "status": "up_to_date",
        "snapshot": {
            "scanStartedAt": REPLAY_NOW_MS - 125_000,
            "measuredAt": REPLAY_NOW_MS - 120_000,
            "durationMs": 5_000,
            "full": False,
            "sizeKind": "private",
            "skippedEntryCount": 0,
            "notMeasuredCount": 0,
            "totals": {
                "activeSessionsBytes": 6_100 * _MB,
                "archivedSessionsBytes": 1_400 * _MB,
                "leftoverWorktreesBytes": 2_300 * _MB,
                "otherVibeDataBytes": 7_800 * _MB,
                "totalBytes": 17_600 * _MB,
            },
            "sessions": rows,
            "worktrees": copy.deepcopy(_WORKTREES),
            "other": [],
            "archiveSuggestion": {
                "inactiveDays": 30,
                "sessionIds": ids,
                "reclaimableBytes": reclaim,
            },
        },
        "disk": {
            "totalBytes": 494 * _GB,
            "freeBytes": (14 if low else 99) * _GB,
            "lowThresholdBytes": 20 * _GB,
            "isLow": low,
            "readAt": REPLAY_NOW_MS,
        },
        "stale": False,
    }


def updated(
    answer: dict[str, Any], *, complete: bool = True, scan: int | None = None
) -> dict[str, Any]:
    """The `storage/usage/updated` notification carrying `answer`, from scan `scan` when given."""
    ids = {} if scan is None else {"scanId": scan}
    return {
        "method": "storage/usage/updated",
        "params": {**answer, "complete": complete, **ids},
    }


def refreshing(answer: dict[str, Any], *, scan: int | None = None) -> dict[str, Any]:
    """The `refreshing` update a scan publishes as it starts."""
    return updated({**answer, "status": "refreshing"}, scan=scan)


def archived(
    answer: dict[str, Any],
    ids: list[str],
    *,
    freed: int = 0,
    worktrees_removed: bool = True,
) -> dict[str, Any]:
    """`answer` after archiving `ids`: rows marked archived, suggestion emptied, total lowered.

    The worktrees the archive frees are gone too, unless `worktrees_removed`
    is false: the reaper had not removed them yet when the scan ran.
    """
    after = copy.deepcopy(answer)
    snapshot = after["snapshot"]
    for row in snapshot["sessions"]:
        if row["sessionId"] in ids:
            row["archived"] = True
    if worktrees_removed:
        archived_ids = {r["sessionId"] for r in snapshot["sessions"] if r["archived"]}
        snapshot["worktrees"] = [
            w
            for w in snapshot["worktrees"]
            if not (
                any(s in ids for s in w["sessionIds"])
                and all(s in archived_ids for s in w["sessionIds"])
            )
        ]
    snapshot["archiveSuggestion"] = {
        "inactiveDays": 30,
        "sessionIds": [],
        "reclaimableBytes": 0,
    }
    snapshot["totals"]["totalBytes"] -= freed
    # A scan run after the archives: it starts at the frozen replay clock.
    snapshot["scanStartedAt"] = REPLAY_NOW_MS
    snapshot["measuredAt"] = REPLAY_NOW_MS
    return after


ARCHIVED = {"archivedAt": REPLAY_NOW_MS}


def refused(sid: str) -> dict[str, Any]:
    """A `session/archive` refusal: the session works."""
    return {
        "error": {
            "code": "conflict",
            "message": "The session is working.",
            "data": {"refusal": "busy"},
        }
    }
