"""One PR: the rail paints its state dot and label; hovering lights it, a click opens the PR window under the rail and Enter opens the link."""

from __future__ import annotations

from e2e.app_server.config import ROWS
from e2e.app_server.events import artifact
from e2e.app_server.scenario import Action, Timeline, take_snapshot
from scenarios.artifacts._rail import FOOTER, click_chip, hover_chip

_LISTED_URL = "https://github.com/mistral-ai/vibe/pull/123"
_LISTED = artifact(
    "artifact-pr-123",
    number=123,
    url=_LISTED_URL,
    discovered_at="2026-08-27T09:00:00Z",
    display_title="Fix the footer",
)
_LISTED["details"].update({"state": "open", "prStatus": "mergeable"})

handshake = {
    "artifacts/list": {"artifacts": [_LISTED]},
    "artifacts/sync": {},
    "artifacts/markViewed": {},
}
request_methods = {"artifacts/list", "artifacts/sync", "artifacts/markViewed"}
expected_actions = [Action("open_url", _LISTED_URL)]
screen_contains = ("● PR #123",)
screen_excludes = ("Pull Requests",)
screen_rows = {ROWS - 1: FOOTER}

_CHIP = "● PR #123"

timeline: Timeline = [
    hover_chip(_CHIP),
    take_snapshot("hover"),
    click_chip(_CHIP),
    take_snapshot("opened"),
    "\r",
]
