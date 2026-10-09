"""A transcript copy under the VS Code promo reads the rows the drag covered, not shifted ones."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# The promo needs a VS Code-family terminal; a fresh VIBE_HOME shows it once.
env = {"TERM_PROGRAM": "vscode", "SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}

_PROMPT = "say hello"
_ANSWER = "Hello there."
# Drag from the prompt's first letter to the end of the answer (1-based SGR).
_SELECT = "\x1b[<0;3;13M\x1b[<32;120;16M\x1b[<0;120;16m"
expected_clipboard = f"{_PROMPT}\n{_ANSWER}"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _SELECT,
]
