"""Shared turns for the sticky prompt scenarios."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

WHEEL_UP = "\x1b[<64;60;10M"


def tall_reply(tag: str, lines: int) -> str:
    return "\n\n".join(f"{tag}{index:02d}" for index in range(1, lines + 1))


def turn(prompt: str, reply: str, *, typed: str | None = None) -> Timeline:
    return [
        f"{typed or prompt}\r",
        turn_started(),
        user_msg(prompt),
        assistant_msg(reply),
        turn_completed(),
    ]
