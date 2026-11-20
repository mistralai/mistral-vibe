"""A copy notice sits on the row above the rail, which keeps every item."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import SETUP, handshake

__all__ = ["handshake"]

env = {"SSH_TTY": "/dev/pts/0"}

_DOUBLE_CLICK = "\x1b[<0;6;38M\x1b[<0;6;38m" * 2

timeline: Timeline = [*SETUP, "a draft in the composer", _DOUBLE_CLICK, take_snapshot()]
expected_clipboard = "draft"
screen_contains = (
    "Copied · if paste fails",
    "◐ 1 agent · ▪ 2 tasks · ▶ goal 4m · ≡ 1/3",
)
