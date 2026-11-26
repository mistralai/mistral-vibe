"""A click on the rail chip opens the PR window; a click highlights a row and a click on the highlighted row opens its link."""

from __future__ import annotations

from e2e.app_server.events import artifact
from e2e.app_server.scenario import Action, Timeline
from scenarios.artifacts._rail import RAIL_ROW, click_chip

_LISTED_URL = "https://github.com/mistral-ai/vibe/pull/123"
_LIVE_URL = "https://github.com/mistral-ai/vibe/pull/124"
_OLDER = artifact(
    "artifact-pr-123",
    number=123,
    url=_LISTED_URL,
    discovered_at="2026-08-27T09:00:00Z",
    display_title="Fix the footer",
)
_NEWER = artifact(
    "artifact-pr-124",
    number=124,
    url=_LIVE_URL,
    discovered_at="2026-08-27T10:00:00Z",
    display_title="Rework the footer",
)

handshake = {
    "artifacts/list": {"artifacts": [_OLDER, _NEWER]},
    "artifacts/sync": {},
    "artifacts/markViewed": {},
}
request_methods = {"artifacts/list", "artifacts/sync", "artifacts/markViewed"}

# The window lists latest first, so the second row is the older PR.
expected_actions = [Action("open_url", _LISTED_URL)]
screen_contains = ("● 2 PRs",)
screen_excludes = ("Pull Requests",)

# The window opens right under the rail: its border, then the latest PR, then the older one.
_OLDER_ROW = RAIL_ROW + 3
_ROW_CLICK = f"\x1b[<0;10;{_OLDER_ROW}M\x1b[<0;10;{_OLDER_ROW}m"

# A row has several actions (Enter, `c`, `f`): the first click highlights, the second opens.
timeline: Timeline = [click_chip("● 2 PRs"), _ROW_CLICK, _ROW_CLICK]
