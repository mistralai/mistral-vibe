"""Ctrl+O keeps a pinned view on the latest message and a scrolled-up view on its top entry."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

screen_rows = {"rust": {1: "  alpha reply 1", 15: "⏵ Ran 3 commands"}}

_WHEEL_UP = "\x1b[<64;60;10M"
_CTRL_O = "\x0f"


def _turn(tag: str) -> Timeline:
    prompt = f"check {tag}"
    return [
        f"{prompt}\r",
        turn_started(),
        user_msg(prompt),
        *(
            bash(
                f"cat {tag}{index}.txt",
                f"{tag}{index} a\n{tag}{index} b\n{tag}{index} c",
            )
            for index in range(1, 4)
        ),
        assistant_msg("\n\n".join(f"{tag} reply {index}" for index in range(1, 6))),
        turn_completed(),
    ]


timeline: Timeline = [
    *_turn("alpha"),
    *_turn("beta"),
    *_turn("gamma"),
    _CTRL_O,
    _CTRL_O,
    _WHEEL_UP * 5,
    _CTRL_O,
    _CTRL_O,
]
