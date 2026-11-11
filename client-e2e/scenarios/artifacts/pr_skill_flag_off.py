"""Without `enable_pr_monitoring` the footer hides the PR, and `/pr` names the flag even over a skill named `pr`."""

from __future__ import annotations

from e2e.app_server.events import artifact
from e2e.app_server.scenario import Timeline

_LISTED = artifact(
    "artifact-pr-123",
    number=123,
    url="https://github.com/mistral-ai/vibe/pull/123",
    discovered_at="2026-08-27T09:00:00Z",
)
_LISTED["details"].update({"state": "open", "prStatus": "mergeable"})

_SKILL = {
    "name": "pr",
    "description": "Open a pull request for my branch.",
    "prompt": "",
    "userInvocable": True,
    "source": "local",
}

handshake = {
    "runtime/read": {
        "runtime": {"config": {"enablePrMonitoring": False}, "skills": [_SKILL]}
    },
    "artifacts/list": {"artifacts": [_LISTED]},
}
screen_contains = ("Turn on enable_pr_monitoring to use /pr",)
screen_excludes = ("PR #123",)

timeline: Timeline = ["/pr\r"]
