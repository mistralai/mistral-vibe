"""Selection copies content while excluding renderer-owned borders."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}

_PRESS = "\x1b[<0;1;25M"
_DRAG = "\x1b[<32;120;33M"
_RELEASE = "\x1b[<0;120;33m"

clipboard_contains = (
    "copy structured content",
    "First component keeps literal ─ and │ characters.",
    "Quoted component.",
    "Last component.",
)
clipboard_excludes = ("────────", "▌")

timeline: Timeline = [
    "copy structured content\r",
    turn_started(),
    user_msg("copy structured content"),
    assistant_msg(
        "First component keeps literal ─ and │ characters."
        "\n\n> Quoted component.\n\nLast component."
    ),
    turn_completed(),
    _PRESS + _DRAG + _RELEASE,
]
