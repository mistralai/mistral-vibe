"""Dragging after a triple press in the composer snaps the range to whole lines."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}

_PRESS = "\x1b[<0;10;36M\x1b[<0;10;36m"
_DRAG = "\x1b[<0;10;36M\x1b[<32;19;36M\x1b[<0;19;36m"

timeline: Timeline = ["a draft in the composer", f"{_PRESS}{_PRESS}{_DRAG}"]
