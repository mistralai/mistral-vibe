"""Double-clicking a status bar word then Cmd+C copies it with autocopy off."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}

handshake = {
    "config/read": {"config": {"autocopyToClipboard": False}},
    "runtime/read": {"runtime": {"config": {"autocopyToClipboard": False}}},
}

# The bottom bar is the last terminal row (40); "workdir" sits at columns 7-13
# of "/test/workdir [PID 0]", so a click at column 9 lands inside the word.
_ROW = 40
_CLICK = f"\x1b[<0;9;{_ROW}M\x1b[<0;9;{_ROW}m"
_DOUBLE_CLICK = _CLICK * 2
_COMMAND_C = "\x1b[99;9u"

clipboard_contains = ("workdir",)
clipboard_excludes = ("/test", "PID")

timeline: Timeline = [_DOUBLE_CLICK, _COMMAND_C]
