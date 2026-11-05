"""One PR: the bar paints a state dot and its link after the path; hovering highlights it and clicking opens it."""

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
_LISTED["details"].update({"state": "open", "prStatus": "mergeable"})

handshake = {"artifacts/list": {"artifacts": [_LISTED]}}
request_methods = {"artifacts/list"}
expected_actions = [Action("open_url", _LISTED_URL)]
screen_excludes = ("PR #124",)

_LEFT = "/test/workdir ⧉  "
_LINK = "● PR #123 mergeable"
_RIGHT = "ctx        0/600k"
screen_rows = {ROWS - 1: (_LEFT + _LINK).ljust(COLUMNS - len(_RIGHT)) + _RIGHT}

# SGR mouse coordinates are 1-indexed; the bar is the last row.
_CLICK_COL = len(_LEFT) + 3
_HOVER = f"\x1b[<35;{_CLICK_COL};{ROWS}M"
_CLICK = f"\x1b[<0;{_CLICK_COL};{ROWS}M\x1b[<0;{_CLICK_COL};{ROWS}m"

# The hover frame paints the link's highlight before the click opens it.
timeline: Timeline = [_HOVER, _CLICK]
