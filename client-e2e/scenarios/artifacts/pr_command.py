"""`/pr` lists the PRs, names why one is blocked, opens its failing check with `c` and drafts a fix prompt with `f`."""

from __future__ import annotations

from e2e.app_server.events import artifact
from e2e.app_server.scenario import Action, Timeline

_CHECK_URL = "https://github.com/mistral-ai/vibe/actions/runs/1"
_BLOCKED = artifact(
    "artifact-pr-124",
    number=124,
    url="https://github.com/mistral-ai/vibe/pull/124",
    discovered_at="2026-08-27T10:00:00Z",
    display_title="Rework the footer",
)
_BLOCKED["details"] |= {
    "state": "open",
    "prStatus": "action_needed",
    "prStatusReason": "checks_failing",
    "checks": {
        "state": "fail",
        "checks": [
            {"name": "build", "status": "pass"},
            {"name": "lint", "status": "fail", "url": _CHECK_URL},
        ],
    },
}

handshake = {
    "artifacts/list": {"artifacts": [_BLOCKED]},
    "artifacts/sync": {},
    "artifacts/markViewed": {},
}
request_methods = {"artifacts/list", "artifacts/sync", "artifacts/markViewed"}
expected_actions = [Action("open_url", _CHECK_URL)]
screen_contains = (
    "Fix the failing CI checks on PR #124 (https://github.com/mistral-ai/vibe/pull/124): lint.",
)

timeline: Timeline = ["/pr\r", "c", "f"]
