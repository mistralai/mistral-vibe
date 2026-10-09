"""Resumed inline images get back their `[Image #N]` name and open from a temporary file."""

from __future__ import annotations

import re

from e2e.app_server.events import (
    assistant_msg,
    inline_image_attachment,
    user_msg_with_images,
)
from e2e.app_server.scenario import Action, AppServerEvent, Timeline

_RESUMED_SESSION = "00000000-0000-4000-8000-000000000003"


def _history_entry(event: AppServerEvent, entry_id: str) -> dict[str, object]:
    """Repoint a builder's entry at the resumed session's static history."""
    entry = event["params"]["entry"]
    entry["id"] = entry_id
    entry["sessionId"] = _RESUMED_SESSION
    entry["turnId"] = None
    return entry


_HISTORY = [
    _history_entry(
        user_msg_with_images(
            "compare [Image #1] with [Image #2]",
            [inline_image_attachment("image"), inline_image_attachment("image")],
        ),
        "compare",
    ),
    _history_entry(
        user_msg_with_images(
            "and @/tmp/vibe-e2e-old.png", [inline_image_attachment("image")]
        ),
        "older",
    ),
    _history_entry(assistant_msg("They differ."), "reply"),
]

handshake = {
    "session/resume": {"state": {"history": _HISTORY}},
    "session/read": {"state": {"history": _HISTORY}},
}

client_args = ("--continue",)
# An existing temporary directory pins where the randomly named files land.
env = {"TMPDIR": "/tmp"}
_OPENED = re.compile(r"file:///tmp/vibe-image-[^/]+\.png")
expected_actions = {"rust": [Action("open_url", _OPENED), Action("open_url", _OPENED)]}


def _click(column: int, row: int) -> str:
    """SGR press and release of the left button at a 1-based cell."""
    return f"\x1b[<0;{column};{row}M\x1b[<0;{column};{row}m"


capture_startup = False
screen_contains = {
    "rust": (
        "┝ attached image: [Image #1]",
        "└ attached image: [Image #2]",
        "└ attached image: /tmp/vibe-e2e-old.png",
    )
}

# Rows 13 and 14 hold the `[Image #1]` and `[Image #2]` links from column 21.
timeline: Timeline = ["x", _click(24, 13), _click(24, 14)]
