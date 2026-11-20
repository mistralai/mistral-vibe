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
    "storage/leftovers/clean",
    "storage/sessions/delete",
}

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
        "worktreeIds": [] if worktree is None else [worktree],
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


SPIKE_WT = "dashboard-aaaaaaaaaaaa/old-spike"
RETRY_WT = "vortex-cccccccccccc/retry"
TARGET_DIR = "dashboard-aaaaaaaaaaaa/target"
SCRATCH_DIR = "santa-bbbbbbbbbbbb/scratch"

# Worktrees no session claims, offered by `leftoverCleanup` with the unknown folders below.
_LEFTOVER_WORKTREES = [
    _worktree(SPIKE_WT, "dashboard", "old-spike", 640 * _MB, [], leftover=True),
    _worktree(RETRY_WT, "vortex", "retry-policy", 120 * _MB, [], leftover=True),
]
_UNKNOWN_FOLDERS = [
    {
        "id": TARGET_DIR,
        "path": f"/home/user/.vibe/worktrees/{TARGET_DIR}",
        "bytes": 2_100 * _MB,
    },
    {
        "id": SCRATCH_DIR,
        "path": f"/home/user/.vibe/worktrees/{SCRATCH_DIR}",
        "bytes": 5 * _MB,
    },
]


def _leftover_cleanup(leftovers: bool) -> dict[str, Any]:
    worktrees = _LEFTOVER_WORKTREES if leftovers else []
    folders = _UNKNOWN_FOLDERS if leftovers else []
    return {
        "worktreeIds": [w["id"] for w in worktrees],
        "unknownFolders": copy.deepcopy(folders),
        "reclaimableBytes": sum(w["bytes"] for w in worktrees)
        + sum(f["bytes"] for f in folders),
    }


def delete_bytes(row: dict[str, Any]) -> int:
    """What deleting `row` alone frees, as the server reads it: its history and the worktrees only it claims."""
    sid = row["sessionId"]
    return row["historyBytes"] + sum(
        w["bytes"] for w in _WORKTREES if w["sessionIds"] == [sid]
    )


def sessions_deleted(
    removed: list[str],
    *,
    kept: list[dict[str, str]] | None = None,
    worktrees: list[str] | None = None,
    freed: int = 0,
) -> dict[str, Any]:
    """A `storage/sessions/delete` answer."""
    return {
        "removed": removed,
        "kept": kept or [],
        "removedWorktreeIds": worktrees or [],
        "keptWorktrees": [],
        "freedBytes": freed,
    }


def flagged(handshake: dict[str, Any]) -> dict[str, Any]:
    """`handshake` with the storage flag on."""
    return {**FLAG_ON, **handshake}


def usage(
    *,
    low: bool = False,
    archived_ids: tuple[str, ...] = (),
    sessions: list[dict[str, Any]] | None = None,
    suggested: list[str] | None = None,
    delete_suggested: list[str] | None = None,
    leftovers: bool = False,
    unknown_delete_bytes: bool = False,
) -> dict[str, Any]:
    """A `storage/usage/read` answer; `low` makes the disk almost full, `archived_ids` archives rows, `delete_suggested` fills `deleteSuggestion`, `leftovers` fills `leftoverCleanup`, `unknown_delete_bytes` sends `deleteBytes: null`."""
    rows = copy.deepcopy(_SESSIONS if sessions is None else sessions)
    for row in rows:
        row["archived"] = row["sessionId"] in archived_ids
        row["deleteBytes"] = None if unknown_delete_bytes else delete_bytes(row)
    ids = SUGGESTED if suggested is None else suggested
    reclaim = sum(
        w["bytes"]
        for w in _WORKTREES
        if w["sessionIds"] and all(s in ids for s in w["sessionIds"])
    )
    doomed = delete_suggested or []
    delete_reclaim = sum(
        r["historyBytes"] for r in rows if r["sessionId"] in doomed
    ) + sum(
        w["bytes"]
        for w in _WORKTREES
        if w["sessionIds"] and all(s in doomed for s in w["sessionIds"])
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
            "worktrees": copy.deepcopy(
                _WORKTREES + (_LEFTOVER_WORKTREES if leftovers else [])
            ),
            "other": [],
            "archiveSuggestion": {
                "inactiveDays": 30,
                "sessionIds": ids,
                "reclaimableBytes": reclaim,
            },
            "deleteSuggestion": {
                "inactiveDays": 30,
                "sessionIds": doomed,
                "reclaimableBytes": delete_reclaim,
            },
            "leftoverCleanup": _leftover_cleanup(leftovers),
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


def cleaned(answer: dict[str, Any], removed: list[str]) -> dict[str, Any]:
    """`answer` after `storage/leftovers/clean` removed `removed`: the scan the server starts finds them gone."""
    after = copy.deepcopy(answer)
    snapshot = after["snapshot"]
    cleanup = snapshot["leftoverCleanup"]
    snapshot["worktrees"] = [w for w in snapshot["worktrees"] if w["id"] not in removed]
    gone = [f for f in cleanup["unknownFolders"] if f["id"] in removed]
    cleanup["worktreeIds"] = [i for i in cleanup["worktreeIds"] if i not in removed]
    cleanup["unknownFolders"] = [f for f in cleanup["unknownFolders"] if f not in gone]
    worktrees = [w for w in snapshot["worktrees"] if w["id"] in cleanup["worktreeIds"]]
    cleanup["reclaimableBytes"] = sum(w["bytes"] for w in worktrees) + sum(
        f["bytes"] for f in cleanup["unknownFolders"]
    )
    snapshot["scanStartedAt"] = REPLAY_NOW_MS
    snapshot["measuredAt"] = REPLAY_NOW_MS
    return after
