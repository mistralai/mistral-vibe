"""A PR created while the modal is open joins the list without moving the highlight."""

from __future__ import annotations

from e2e.app_server.config import ROWS
from e2e.app_server.events import (
    artifact,
    artifact_created,
    assistant_msg,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Action, Timeline


def _pr(number: int, hour: int) -> dict[str, object]:
    return artifact(
        f"artifact-pr-{number}",
        number=number,
        url=f"https://github.com/mistral-ai/vibe/pull/{number}",
        discovered_at=f"2026-08-27T{hour:02d}:00:00Z",
        display_title=f"Change {number}",
    )


handshake = {"artifacts/list": {"artifacts": [_pr(123, 9), _pr(124, 10)]}}
request_methods = {"artifacts/list"}

# Enter after the live PR still opens the older PR the highlight was on.
expected_actions = [Action("open_url", "https://github.com/mistral-ai/vibe/pull/123")]
screen_contains = ("PRs (3)",)

_LABEL = "PRs (2)"
_CLICK_COL = len("/test/workdir  ") + len(_LABEL) // 2 + 1
_BAR_CLICK = f"\x1b[<0;{_CLICK_COL};{ROWS}M\x1b[<0;{_CLICK_COL};{ROWS}m"

timeline: Timeline = [
    "open the PR\r",
    turn_started(),
    user_msg("open the PR"),
    assistant_msg("Opening it."),
    turn_completed(),
    artifact_created(_pr(125, 11)),
    {"release_after": 5},  # the turn, its queue drain included; the PR waits
    _BAR_CLICK,
    "\x1b[B",  # Down: the older PR
    {"release_after": 1},  # the new PR lands while the modal is open
    "\r",
]
