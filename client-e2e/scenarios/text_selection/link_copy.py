"""A dragged markdown link copies its label only, never the terminal's hyperlink escapes."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}

_PROMPT = "give me Paul Vezia's profile"
_ANSWER = "• Paul Vezia — Senior AI Engineer at Publicis. [LinkedIn profile](https://www.linkedin.com)"

# Drag across the answer row, link included (1-based SGR).
_SELECT = "\x1b[<0;3;11M\x1b[<32;66;11M\x1b[<0;66;11m"
expected_clipboard = "• Paul Vezia — Senior AI Engineer at Publicis. LinkedIn profile"
screen_hyperlinks = (("LinkedIn profile", "https://www.linkedin.com"),)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _SELECT,
]
