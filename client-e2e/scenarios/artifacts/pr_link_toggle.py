"""A click on the footer's PR link opens `/pr` and refreshes it; a second click closes it, like the context gauge."""

from __future__ import annotations

from e2e.app_server.config import ROWS
from e2e.app_server.events import artifact
from e2e.app_server.scenario import Timeline

_LISTED = artifact(
    "artifact-pr-123",
    number=123,
    url="https://github.com/mistral-ai/vibe/pull/123",
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
screen_contains = ("● PR #123 mergeable",)
screen_excludes = ("Pull Requests", "Fix the footer")

# SGR mouse coordinates are 1-indexed; the bar is the last row.
_LINK_COL = len("/test/workdir  ") + 3
_CLICK = f"\x1b[<0;{_LINK_COL};{ROWS}M\x1b[<0;{_LINK_COL};{ROWS}m"

timeline: Timeline = [_CLICK, _CLICK]
