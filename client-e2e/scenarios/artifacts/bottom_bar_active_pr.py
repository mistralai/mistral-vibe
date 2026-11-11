"""Merged and closed PRs give way to the open one; an unseen PR reads bold until `/pr` shows it, and opening `/pr` refreshes the status."""

from __future__ import annotations

from typing import Any

from e2e.app_server.config import ROWS
from e2e.app_server.events import artifact, artifact_updated
from e2e.app_server.scenario import Timeline

_VIEWED = "2026-08-27T12:00:00Z"


def _pr(number: int, hour: int, **details: Any) -> dict[str, Any]:
    pr = artifact(
        f"artifact-pr-{number}",
        number=number,
        url=f"https://github.com/mistral-ai/vibe/pull/{number}",
        discovered_at=f"2026-08-27T{hour:02d}:00:00Z",
        display_title=f"Change {number}",
    )
    pr["viewedAt"] = _VIEWED
    pr["details"] |= details
    return pr


_RUNNING = _pr(
    124, 10, state="open", prStatus="waiting", prStatusReason="checks_pending"
)
_RUNNING["viewedAt"] = None
_FAILED = _pr(
    124, 10, state="open", prStatus="action_needed", prStatusReason="checks_failing"
)
_FAILED_UNSEEN = _FAILED | {"viewedAt": None}

handshake = {
    "artifacts/list": {
        "artifacts": [
            _pr(122, 8, state="merged"),
            _RUNNING,
            _pr(123, 9, state="closed"),
        ]
    },
    "artifacts/sync": {},
    "artifacts/markViewed": {},
}
on_request = {
    "artifacts/sync": [artifact_updated(_FAILED_UNSEEN)],
    "artifacts/markViewed": [artifact_updated(_FAILED)],
}
request_methods = {"artifacts/list", "artifacts/sync", "artifacts/markViewed"}
screen_contains = ("● PR #124 CI failing",)
screen_excludes = ("CI running", "PRs (3)")

_LINK_COL = len("/test/workdir  ") + 3
_CLICK = f"\x1b[<0;{_LINK_COL};{ROWS}M\x1b[<0;{_LINK_COL};{ROWS}m"

# A bold `CI running` link; opening `/pr` refreshes it to `CI failing`; closing it leaves it seen.
timeline: Timeline = ["a", _CLICK, _CLICK]
