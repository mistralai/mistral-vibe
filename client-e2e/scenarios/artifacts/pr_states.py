"""Clicking the footer link opens `/pr`: one row per PR with its number, title and state colored by lifecycle, nothing else."""

from __future__ import annotations

from e2e.app_server.config import ROWS
from e2e.app_server.events import artifact
from e2e.app_server.scenario import Timeline, take_snapshot


def _pr(number: int, hour: int, title: str, state: str) -> dict:
    pr = artifact(
        f"artifact-pr-{number}",
        number=number,
        url=f"https://github.com/mistral-ai/vibe/pull/{number}",
        discovered_at=f"2026-08-27T{hour:02}:00:00Z",
        display_title=title,
    )
    pr["details"] |= {"state": state}
    return pr


handshake = {
    "artifacts/list": {
        "artifacts": [
            _pr(122, 8, "Drop the old bar", "closed"),
            _pr(123, 9, "Fix the footer", "merged"),
            _pr(124, 10, "Rework the footer", "open"),
            _pr(125, 11, "Draft the docs", "draft"),
        ]
    },
    "artifacts/sync": {},
    "artifacts/markViewed": {},
}
request_methods = {"artifacts/list", "artifacts/sync", "artifacts/markViewed"}
screen_contains = (
    "Pull Requests",
    "● #125  Draft the docs     draft",
    "● #124  Rework the footer  open",
    "● #123  Fix the footer     merged",
    "● #122  Drop the old bar   closed",
)
screen_excludes = ("https://github.com/mistral-ai/vibe/pull/",)

# The bar counts the open and draft PRs; merged and closed ones only show in the list.
_LABEL = "PRs (2)"
_CLICK_COL = len("/test/workdir  ") + len(_LABEL) // 2 + 1
_BAR_CLICK = f"\x1b[<0;{_CLICK_COL};{ROWS}M\x1b[<0;{_CLICK_COL};{ROWS}m"

timeline: Timeline = [_BAR_CLICK, take_snapshot()]
