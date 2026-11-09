"""`/clear` drops the old session's PRs from the bar and reads the new session's."""

from __future__ import annotations

from e2e.app_server.config import COLUMNS, ROWS
from e2e.app_server.events import CLEARED_SESSION_ID, artifact, cleared_state
from e2e.app_server.scenario import Timeline

_OLD = [
    artifact(
        f"artifact-pr-{number}",
        number=number,
        url=f"https://github.com/mistral-ai/vibe/pull/{number}",
        discovered_at=f"2026-08-27T{number - 114:02d}:00:00Z",
        display_title=f"Old change {number}",
    )
    for number in (123, 124)
]
_NEW = artifact(
    "artifact-pr-200",
    number=200,
    url="https://github.com/mistral-ai/vibe/pull/200",
    discovered_at="2026-08-28T09:00:00Z",
    display_title="New change",
    session_id=CLEARED_SESSION_ID,
)

handshake = {
    "artifacts/list": {"artifacts": _OLD},
    f"artifacts/list#{CLEARED_SESSION_ID}": {"artifacts": [_NEW]},
    "session/history/clear": {"state": cleared_state()},
}
request_methods = {"artifacts/list", "session/history/clear"}

screen_contains = ("New conversation started.", "PR #200")
screen_excludes = ("PRs (2)", "PR #123", "PR #124")

_LEFT = "/test/workdir ⧉  "
_LINK = "● PR #200"
_RIGHT = "ctx        0/600k"
screen_rows = {ROWS - 1: (_LEFT + _LINK).ljust(COLUMNS - len(_RIGHT)) + _RIGHT}

timeline: Timeline = ["/clear\r"]
