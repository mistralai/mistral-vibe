"""Dragging the status bar then Cmd+C copies its path with autocopy off."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}


handshake = {
    "config/read": {"config": {"autocopyToClipboard": False}},
    "runtime/read": {"runtime": {"config": {"autocopyToClipboard": False}}},
}

# The bottom bar is the last terminal row (40); under replay it reads
# "/test/workdir" from column 1 through column 13.
_ROW = 40
_PRESS = f"\x1b[<0;1;{_ROW}M"
_DRAG = f"\x1b[<32;13;{_ROW}M"
_RELEASE = f"\x1b[<0;13;{_ROW}m"
_COMMAND_C = "\x1b[99;9u"

clipboard_contains = ("/test/workdir",)

timeline: Timeline = [_PRESS + _DRAG + _RELEASE, _COMMAND_C]
