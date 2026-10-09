"""Multi-line free text wraps, caps at five rows, scrolls with the caret and takes clicks."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    ask_user_question_answered,
    paste,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
capture_steps = {1, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18}
request_methods = {"callback/result"}

_QUESTION = "Which database should we use for this project?"
_QUESTIONS = [
    {
        "question": _QUESTION,
        "options": [
            {"label": "PostgreSQL", "description": "Relational database"},
            {"label": "MongoDB", "description": "Document database"},
            {"label": "Redis", "description": "In-memory store"},
        ],
    }
]
_LONG = (
    "We need something embedded, ideally SQLite with WAL enabled, because the CLI "
    "runs offline and must never depend on a database server process."
)
_ANSWER = f"{_LONG}\nAlso: no daemon\nline Xfour\nline five\nline six"

on_request = {
    "callback/result": [
        ask_user_question_answered([
            {"question": _QUESTION, "answer": _ANSWER, "isOther": True}
        ]),
        turn_completed(),
    ]
}

_UP = "\x1b[A"
_DOWN = "\x1b[B"
_LEFT = "\x1b[D"
_SHIFT_ENTER = "\x1b[13;2u"
# Between `line ` and `four`: the unfocused field keeps its scroll at the top.
_CLICK_LINE_FOUR = "\x1b[<0;13;35M\x1b[<0;13;35m"

timeline: Timeline = [
    "which db\r",
    turn_started(),
    user_msg("which db"),
    ask_user_question_added(_QUESTIONS),
    ask_user_question(_QUESTIONS),
    {"release": 5},
    _DOWN,
    _DOWN,
    # The free-text row takes the cursor: the hint gains the newline key.
    _DOWN,
    # A long answer word-wraps under the option prefix instead of overflowing.
    paste(_LONG),
    # Ctrl+J and Shift+Enter both insert a newline; Enter would submit.
    "\n",
    "Also: no daemon",
    _SHIFT_ENTER,
    # Six rows overflow the five-row cap: the field scrolls and shows an up arrow.
    paste("line four\nline five\nline six"),
    # With the caret at the end of the answer, Up goes straight to the option above.
    _UP,
    # Back in the field the caret sits at the end again; step off it to edit rows.
    _DOWN,
    _LEFT,
    # The caret climbs row by row; the field scrolls back and shows a down arrow.
    _UP * 4,
    _UP,
    # On the first row, Up leaves the field for the option above.
    _UP,
    # A click inside the field focuses it with the caret under the pointer.
    _CLICK_LINE_FOUR,
    "X",
    # Submitting the field sends every line as the answer.
    "\r",
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
