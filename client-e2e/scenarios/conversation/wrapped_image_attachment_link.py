"""Scenario: a file attachment label that wraps stays clickable on its continuation row."""

from __future__ import annotations

from urllib.parse import quote

from e2e.app_server.events import (
    assistant_msg,
    file_image_attachment,
    user_msg_with_images,
)
from e2e.app_server.scenario import Action, AppServerEvent, Timeline

_RESUMED_SESSION = "00000000-0000-4000-8000-000000000003"
_PATH = (
    "/opt/data/quarterly reviews/2026/Q3 revenue breakdown by region"
    " and product line with annotated charts final.png"
)


def _history_entry(event: AppServerEvent) -> dict[str, object]:
    """Repoint a builder's entry at the resumed session's static history."""
    entry = event["params"]["entry"]
    entry["sessionId"] = _RESUMED_SESSION
    entry["turnId"] = None
    return entry


_HISTORY = [
    _history_entry(
        user_msg_with_images("look at this", [file_image_attachment(_PATH)])
    ),
    _history_entry(assistant_msg("Nice chart.")),
]

handshake = {
    "session/resume": {"state": {"history": _HISTORY}},
    "session/read": {"state": {"history": _HISTORY}},
}

client_args = ("--continue",)
capture_startup = False

expected_actions = {
    "rust": [Action("open_url", f"file://{quote(_PATH)}")],
    "python": [Action("open_url", f"file://{quote(_PATH)}")],
}


def _click(column: int, row: int) -> str:
    """SGR press and release of the left button at a 1-based cell."""
    return f"\x1b[<0;{column};{row}M\x1b[<0;{column};{row}m"


timeline: Timeline = [
    "x",
    _click(3, 27),  # `charts final.png`, the label's continuation row
]
