"""Ctrl+X then `p` opens the PR window: one row per PR with its number, title and state colored by lifecycle, nothing else."""

from __future__ import annotations

from e2e.app_server.events import artifact
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.artifacts._rail import OPEN_PRS


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
    "Pull Requests 4",
    "● 4 PRs",
    "● #125  Draft the docs     draft",
    "● #124  Rework the footer  open",
    "● #123  Fix the footer     merged",
    "● #122  Drop the old bar   closed",
)
screen_excludes = ("https://github.com/mistral-ai/vibe/pull/",)

# The rail counts every PR its window lists, merged and closed ones included.
timeline: Timeline = [*OPEN_PRS, take_snapshot()]
