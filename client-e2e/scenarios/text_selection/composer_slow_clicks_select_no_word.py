"""Two clicks further apart than the double-click window stay single clicks: no word is selected."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, advance

env = {"SSH_TTY": "/dev/pts/0"}

_CLICK = "\x1b[<0;10;36M\x1b[<0;10;36m"

timeline: Timeline = ["a draft in the composer", _CLICK, advance(600), _CLICK]
capture_steps = {3}
screen_excludes = ("Copied",)
