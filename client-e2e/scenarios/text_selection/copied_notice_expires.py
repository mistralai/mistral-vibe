"""The copy notice stays up until its four seconds have passed, then clears."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, advance

env = {"SSH_TTY": "/dev/pts/0"}

_DOUBLE_CLICK = "\x1b[<0;10;36M\x1b[<0;10;36m" * 2

timeline: Timeline = [
    "a draft in the composer",
    _DOUBLE_CLICK,
    advance(3900),
    advance(100),
]
capture_steps = {1, 2, 3}
expected_clipboard = "draft"
screen_excludes = ("Copied",)
