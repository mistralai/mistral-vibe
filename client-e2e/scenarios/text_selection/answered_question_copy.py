"""Copying an answered question row leaves its settled check mark out of the clipboard."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question_effect,
    assistant_msg,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}

_QUESTION = "Which part of the repo should I focus on?"

# The answered row lands on SGR row 15; drag across it from the first column.
_SELECT = "\x1b[<0;1;15M\x1b[<32;120;15M\x1b[<0;120;15m"
expected_clipboard = f'Answered "{_QUESTION}" → vibe/core'

timeline: Timeline = [
    "where should I look\r",
    turn_started(),
    user_msg("where should I look"),
    ask_user_question_effect(_QUESTION, "vibe/core"),
    assistant_msg("Focusing on vibe/core."),
    turn_completed(),
    _SELECT,
]
