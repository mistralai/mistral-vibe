"""Tool output and diff rows wrapped at the terminal width copy as their source lines."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    edit_file,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}

_PROMPT = "run and edit"
_STDOUT = (
    "stdout line long enough to wrap inside the bordered tool result, so the copy must"
    " rejoin it into the single line the command printed"
)
_OLD = "short = 1\n"
_NEW = (
    "long = 'a diff line long enough to wrap under its line-number gutter, which the copy"
    " rejoins into the one source line it is'\n"
)

_EXPAND_GROUP = "\x1b[<0;1;15M\x1b[<0;1;15m"
# Expanding the edit leaves the bash row above it on row 16.
_EXPAND_EDIT = "\x1b[<0;5;17M\x1b[<0;5;17m"
_EXPAND_BASH = "\x1b[<0;7;16M\x1b[<0;7;16m"
# Drag from the stdout's first letter to the end of the wrapped diff row.
_SELECT = "\x1b[<0;5;17M\x1b[<32;120;22M\x1b[<0;120;22m"
# Each wrapped source line comes back whole, with no row break or hang indent.
clipboard_contains = (_STDOUT, _NEW.strip())

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash("echo long", _STDOUT),
    edit_file("notes.py", [(1, _OLD, _NEW)]),
    assistant_msg("Done."),
    turn_completed(),
    _EXPAND_GROUP,
    _EXPAND_EDIT,
    _EXPAND_BASH,
    _SELECT,
]
