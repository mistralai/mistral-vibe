"""A Khmer cluster the terminal draws wider than unicode-width never pushes the scrollbar off its column."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_KHMER = "ប្រាំ បួនដណ្ដប់"
_REPLY = "\n\n".join([*(f"line {index}" for index in range(30)), _KHMER, _KHMER])

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg(_REPLY),
    turn_completed(),
]
