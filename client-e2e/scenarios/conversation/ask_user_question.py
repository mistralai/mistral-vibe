"""Navigate a single-question `ask_user_question` bottom app, including free text."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The turn stays open: the tool blocks on the callback until the user answers,
# so every frame settles while the client is still busy. The typing grace period
# is zeroed so the app takes the input box in the frame it is delivered in.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}
# The prompt frame races the handshake under `SETTLE_BUSY`; capture from the
# released callback onward.
capture_startup = False
capture_steps = {1, 2, 3, 4, 5, 6, 7}
expected_titles = ("? Vibe",) * 7

_DOWN = "\x1b[B"
_UP = "\x1b[A"

timeline: Timeline = [
    "which db\r",
    turn_started(),
    user_msg("which db"),
    # The projector broadcasts the callback history entry, then calls the client.
    # Neither the entry nor its answered state may reach the transcript.
    ask_user_question_added(
        [
            {
                "question": "Which database should we use for this project?",
                "options": [
                    {"label": "PostgreSQL", "description": "Relational database"},
                    {"label": "MongoDB", "description": "Document database"},
                    {"label": "Redis", "description": "In-memory store"},
                ],
            }
        ],
        footer_note="Pick the one you already run in production.",
    ),
    ask_user_question(
        [
            {
                "question": "Which database should we use for this project?",
                "options": [
                    {"label": "PostgreSQL", "description": "Relational database"},
                    {"label": "MongoDB", "description": "Document database"},
                    {"label": "Redis", "description": "In-memory store"},
                ],
            }
        ],
        footer_note="Pick the one you already run in production.",
    ),
    {"release": 5},
    _DOWN,
    _DOWN,
    _UP,
    # Two more downs park the cursor on the free-text row, which then takes keys.
    _DOWN,
    _DOWN,
    "SQLite",
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
