"""A bottom question app leaves visible transcript disclosures clickable."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    read_file,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
capture_steps = {1, 2}
settle_per_key = True

_PROMPT = "inspect the file, then ask me a question"
_CONTENT = "   1→first selected line\n   2→second selected line"
_QUESTION = [
    {"question": "Should I continue?", "options": [{"label": "Yes"}, {"label": "No"}]}
]

# The read-file disclosure remains in the transcript above the question app.
_CLICK = "\x1b[<0;1;28M\x1b[<0;1;28m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    read_file("selection.txt", _CONTENT, num_lines=2),
    ask_user_question_added(_QUESTION),
    ask_user_question(_QUESTION),
    {"release": 6},
    _CLICK,
]
