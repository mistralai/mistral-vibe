"""The copy shine crosses a long selection spanning a reply and tool calls."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, advance, take_snapshot

env = {"SSH_TTY": "/dev/pts/0", "VIBE_THEME": "mistral-dark"}
handshake = {
    "config/read": {"config": {"theme": "mistral-dark"}},
    "runtime/read": {"runtime": {"config": {"theme": "mistral-dark"}}},
}

_PRESS = "\x1b[<0;1;3M"
_DRAG = "\x1b[<32;120;30M"
_RELEASE = "\x1b[<0;120;30m"

_LONG = "\n".join(
    f"Line {i}: the light should cross every row of this long reply." for i in range(12)
)

timeline: Timeline = [
    "make it shine\r",
    turn_started(),
    user_msg("make it shine"),
    bash("ls", "a.txt\nb.txt\nc.txt"),
    assistant_msg(_LONG),
    bash("cat a.txt", "hello\nworld"),
    assistant_msg("Done."),
    turn_completed(),
    _PRESS + _DRAG + _RELEASE,
    take_snapshot("copied"),
    advance(100),
    take_snapshot("100ms"),
    advance(100),
    take_snapshot("200ms"),
    advance(100),
    take_snapshot("300ms"),
    advance(100),
    take_snapshot("400ms"),
    advance(500),
    take_snapshot("900ms"),
]
