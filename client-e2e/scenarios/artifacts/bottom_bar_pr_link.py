"""One PR: the bar paints its link, centered; clicking opens it in the browser."""

from __future__ import annotations

from e2e.app_server.config import COLUMNS, ROWS
from e2e.app_server.events import artifact
from e2e.app_server.scenario import Action, Timeline

_LISTED_URL = "https://github.com/mistral-ai/vibe/pull/123"
_LISTED = artifact(
    "artifact-pr-123",
    number=123,
    url=_LISTED_URL,
    discovered_at="2026-08-27T09:00:00Z",
    display_title="Fix the footer",
)

handshake = {"artifacts/list": {"artifacts": [_LISTED]}}
request_methods = {"artifacts/list"}
expected_actions = [Action("open_url", _LISTED_URL)]
screen_contains = ("PR #123",)
screen_excludes = ("PR #124",)

# The replay-pinned bar edges around the centered link.
_LEFT = "/test/workdir"
_RIGHT = "mistral-medium-3.5[medium] · 0/600k tokens (0%)"
_LINK = "PR #123"
_LINK_X = (COLUMNS - len(_LINK)) // 2
screen_rows = {
    ROWS - 1: _LEFT.ljust(_LINK_X)
    + _LINK.ljust(COLUMNS - len(_RIGHT) - _LINK_X)
    + _RIGHT
}

# SGR mouse coordinates are 1-indexed; the bar is the last row.
_CLICK_COL = _LINK_X + len(_LINK) // 2 + 1
_CLICK = f"\x1b[<0;{_CLICK_COL};{ROWS}M\x1b[<0;{_CLICK_COL};{ROWS}m"

timeline: Timeline = [_CLICK]
