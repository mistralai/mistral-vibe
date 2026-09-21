"""Resumed file-backed image attachments link their `~`-collapsed path; clicking one opens it."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    file_image_attachment,
    inline_image_attachment,
    user_msg_with_images,
)
from e2e.app_server.scenario import Action, AppServerEvent, Timeline

_RESUMED_SESSION = "00000000-0000-4000-8000-000000000003"


def _history_entry(event: AppServerEvent) -> dict[str, object]:
    """Repoint a builder's entry at the resumed session's static history."""
    entry = event["params"]["entry"]
    entry["sessionId"] = _RESUMED_SESSION
    entry["turnId"] = None
    return entry


_ATTACHMENTS = [
    file_image_attachment("/home/e2e-user/pics/cat.png"),
    file_image_attachment("/opt/data/charts.png"),
    inline_image_attachment("pasted-chart.png"),
]

_HISTORY = [
    _history_entry(user_msg_with_images("look at these", _ATTACHMENTS)),
    _history_entry(assistant_msg("Nice images.")),
]

handshake = {
    "session/resume": {"state": {"history": _HISTORY}},
    "session/read": {"state": {"history": _HISTORY}},
}

# HOME is pinned so the `~` collapse and the file:// link stay deterministic.
env = {"HOME": "/home/e2e-user"}

client_args = ("--continue",)
# Both clients settle on the resumed transcript only after the attach; the
# startup frames differ by design (rust attaches in-handshake), so the step
# capture after typing `x` carries the comparison.
capture_startup = False

_ATTACHED_ROWS = (
    "└ attached image: ~/pics/cat.png",
    "└ attached image: /opt/data/charts.png",
    "└ attached image: pasted-chart.png",
)

screen_contains = {"rust": _ATTACHED_ROWS, "python": _ATTACHED_ROWS}

_CAT_URL = "file:///home/e2e-user/pics/cat.png"
_CHARTS_URL = "file:///opt/data/charts.png"


def _click(column: int, row: int) -> str:
    """SGR press and release of the left button at a 1-based cell."""
    return f"\x1b[<0;{column};{row}M\x1b[<0;{column};{row}m"


# The Rust attachment links occupy adjacent rows.
expected_actions = {
    "rust": [Action("open_url", _CAT_URL), Action("open_url", _CHARTS_URL)],
    "python": [Action("open_url", _CAT_URL)],
}

timeline: Timeline = [
    "x",
    _click(25, 25),  # rust: the `~/pics/cat.png` link; python: the prompt row
    _click(
        25, 26
    ),  # rust: the `/opt/data/charts.png` link; python: the `~/pics/cat.png` link
]
