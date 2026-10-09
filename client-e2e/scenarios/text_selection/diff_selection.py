"""Selecting an edit diff skips its gutter: line numbers and +/- signs are chrome."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    edit_file,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
# Python renders the diff asynchronously, so the turn's own frame is racy; only
# the two selection steps are compared. Diff content is pinned by conversation/edit_diff.

_PROMPT = "edit the file"
_OLD = "alpha\nbeta\ngamma\ndelta\nepsilon\nzeta\n"
_NEW = "alpha\nBETA\ngamma\ndelta\nEPSILON\nzeta\n"

# A drag starting in the numbered gutter must still select body text only.
# Keep it on one row so selection-triggered copy toasts cannot shift the later
# endpoint while the gesture is in progress.
_DRAG = "\x1b[<0;6;18M\x1b[<32;14;18M\x1b[<0;14;18m"
# A double-click on a diff body word snaps to that word, gutter excluded.
_DOUBLE = "\x1b[<0;11;18M\x1b[<0;11;18m\x1b[<0;11;18M\x1b[<0;11;18m"
# The lone edit stays ungrouped, so its own header row unfolds the diff.
_EXPAND_EDIT = "\x1b[<0;1;15M\x1b[<0;1;15m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    edit_file("notes.txt", [(3, _OLD, _NEW)]),
    assistant_msg("Done."),
    turn_completed(),
    _EXPAND_EDIT,
    _DRAG,
    _DOUBLE,
]

capture_steps = {2, 3}
