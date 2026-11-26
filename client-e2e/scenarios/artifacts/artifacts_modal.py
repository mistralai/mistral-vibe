"""Two PRs: the rail paints a count; Ctrl+X then `p` opens the PR window in the input box's place.

The window lists the PRs latest first. Esc closes it, Ctrl+X `p` reopens it,
Down moves to the older PR, and Enter opens that PR's link.
"""

from __future__ import annotations

from e2e.app_server.config import ROWS
from e2e.app_server.events import artifact
from e2e.app_server.scenario import Action, Timeline
from scenarios.artifacts._rail import ESCAPE, FOOTER, OPEN_PRS

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

# Enter opens the highlighted (older) PR; the window is closed by then.
expected_actions = [Action("open_url", _LISTED_URL)]
screen_contains = ("● 2 PRs",)
screen_excludes = ("Pull Requests",)
# The footer no longer carries the PRs.
screen_rows = {ROWS - 1: FOOTER}

timeline: Timeline = [*OPEN_PRS, ESCAPE, *OPEN_PRS, "\x1b[B", "\r"]
