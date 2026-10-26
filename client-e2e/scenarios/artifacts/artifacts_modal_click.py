"""A click on a modal row opens that artifact's link, like Enter."""

from __future__ import annotations

from e2e.app_server.config import COLUMNS, ROWS
from e2e.app_server.events import artifact
from e2e.app_server.scenario import Action, Timeline

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

handshake = {"artifacts/list": {"artifacts": [_OLDER, _NEWER]}}
request_methods = {"artifacts/list"}

# The modal lists latest first, so the second row is the older PR.
expected_actions = [Action("open_url", _LISTED_URL)]
screen_contains = ("PRs (2)",)
screen_excludes = ("Pull Requests",)

_LEFT = "/test/workdir"
_RIGHT = "mistral-medium-3.5[medium] · 0/600k tokens (0%)"
_LABEL = "PRs (2)"
_LABEL_X = (COLUMNS - len(_LABEL)) // 2
screen_rows = {
    ROWS - 1: _LEFT.ljust(_LABEL_X)
    + _LABEL.ljust(COLUMNS - len(_RIGHT) - _LABEL_X)
    + _RIGHT
}

# SGR mouse coordinates are 1-indexed; the bar is the last row.
_CLICK_COL = _LABEL_X + len(_LABEL) // 2 + 1
_BAR_CLICK = f"\x1b[<0;{_CLICK_COL};{ROWS}M\x1b[<0;{_CLICK_COL};{ROWS}m"

# The modal box is 7 rows tall (2 artifacts + 5 chrome) docked above the bar:
# rows 32-38, options on rows 34 (latest) and 35 (older).
_ROW_CLICK = "\x1b[<0;10;36M\x1b[<0;10;36m"

timeline: Timeline = [_BAR_CLICK, _ROW_CLICK]
