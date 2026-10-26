"""Two PRs: the bar paints a count link; clicking it opens the artifacts modal.

The modal lists the artifacts latest first. Esc closes it, a second click
reopens it, Down moves to the older PR, and Enter opens that PR's link.
"""

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

# Enter opens the highlighted (older) PR; the modal is closed by then.
expected_actions = [Action("open_url", _LISTED_URL)]
screen_contains = ("PRs (2)",)
screen_excludes = ("Pull Requests",)

# The replay-pinned bar edges around the centered count link.
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
# The reopening click lands on another cell of the label: replay keeps the
# click chain alive across steps, and a same-cell press would double-click
# (select the word) instead of clicking.
_REOPEN_COL = _LABEL_X + 2
_BAR_REOPEN = f"\x1b[<0;{_REOPEN_COL};{ROWS}M\x1b[<0;{_REOPEN_COL};{ROWS}m"
_ESCAPE = "\x1b[27u"

timeline: Timeline = [_BAR_CLICK, _ESCAPE, _BAR_REOPEN, "\x1b[B", "\r"]
