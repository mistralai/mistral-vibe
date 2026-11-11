"""Two records of one PR (another id, same PR) show as that one PR, never `PRs (2)` (VIBE-5520)."""

from __future__ import annotations

from e2e.app_server.config import COLUMNS, ROWS
from e2e.app_server.events import artifact, artifact_created
from e2e.app_server.scenario import Timeline


def _record(artifact_id: str, discovered_at: str) -> dict[str, object]:
    pr = artifact(
        artifact_id,
        number=72279,
        url="https://github.com/mistral-ai/vibe/pull/72279",
        discovered_at=discovered_at,
        display_title="Sentry profiling and trace sampling",
    )
    pr["details"] |= {"state": "draft"}
    return pr


handshake = {
    "artifacts/list": {"artifacts": [_record("artifact-a", "2026-08-27T09:00:00Z")]}
}
on_request = {
    "artifacts/list": [artifact_created(_record("artifact-b", "2026-08-27T10:00:00Z"))]
}
request_methods = {"artifacts/list"}
screen_excludes = ("PRs (2)",)

_LEFT = "/test/workdir  "
_LINK = "● PR #72279 draft"
_RIGHT = "ctx        0/600k"
screen_rows = {ROWS - 1: (_LEFT + _LINK).ljust(COLUMNS - len(_RIGHT)) + _RIGHT}

timeline: Timeline = ["a"]
