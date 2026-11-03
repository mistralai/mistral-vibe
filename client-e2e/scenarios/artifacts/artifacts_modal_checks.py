"""Checks sit under the rows, failures first; Down keeps the box height."""

from __future__ import annotations

from e2e.app_server.config import ROWS
from e2e.app_server.events import artifact
from e2e.app_server.scenario import Timeline

_CHECKED = artifact(
    "artifact-pr-124",
    number=124,
    url="https://github.com/mistral-ai/vibe/pull/124",
    discovered_at="2026-08-27T10:00:00Z",
    display_title="Rework the footer",
)
_CHECKED["details"] |= {
    "state": "open",
    "prStatus": "action_needed",
    "checks": {
        "state": "fail",
        "checks": [
            {"name": "build", "status": "pass"},
            {"name": "lint", "status": "fail"},
            {"name": "tests", "status": "pending"},
            {"name": "e2e", "status": "pass"},
            {"name": "deploy", "status": "pass"},
        ],
    },
}
_PLAIN = artifact(
    "artifact-pr-123",
    number=123,
    url="https://github.com/mistral-ai/vibe/pull/123",
    discovered_at="2026-08-27T09:00:00Z",
    display_title="Fix the footer",
)

handshake = {"artifacts/list": {"artifacts": [_PLAIN, _CHECKED]}}
request_methods = {"artifacts/list"}

# After Down the older PR has no checks: its URL still sits on the same row.
screen_contains = ("Pull Requests", "https://github.com/mistral-ai/vibe/pull/123")
screen_excludes = ("✗ lint",)

_LABEL = "PRs (2)"
_CLICK_COL = len("/test/workdir ⧉  ") + len(_LABEL) // 2 + 1
_BAR_CLICK = f"\x1b[<0;{_CLICK_COL};{ROWS}M\x1b[<0;{_CLICK_COL};{ROWS}m"

timeline: Timeline = [_BAR_CLICK, "\x1b[B"]
