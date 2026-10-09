"""Collapsing a result from its body lands the header on the clicked row."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

screen_rows = {"rust": {11: "⏵ Ran cat app.log"}}

_WHEEL_UP = "\x1b[<64;60;10M"
_CTRL_O = "\x0f"
_LOG = "\n".join(f"log line {index:02d}" for index in range(1, 61))


def _reply(tag: str, lines: int) -> str:
    return "\n\n".join(f"{tag} {index:02d}" for index in range(1, lines + 1))


timeline: Timeline = [
    "read the log\r",
    turn_started(),
    user_msg("read the log"),
    bash("cat app.log", _LOG),
    assistant_msg("Read."),
    turn_completed(),
    "next\r",
    turn_started(),
    user_msg("next"),
    assistant_msg(_reply("Next line", 15)),
    turn_completed(),
    _CTRL_O,
    _WHEEL_UP * 30,
    # SGR row 12 hits a log line while the result header is above the viewport.
    "\x1b[<0;10;12M\x1b[<0;10;12m",
]
