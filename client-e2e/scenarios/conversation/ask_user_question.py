"""Navigate a single-question `ask_user_question` bottom app, including free text."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The typing grace period is zeroed so the app takes the input box in the frame
# it is delivered in.
env = {"VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}
expected_titles = (
    "Vibe",
    ">> Vibe",
    "? Vibe",
    "? Vibe",
    "? Vibe",
    "? Vibe",
    "? Vibe",
    "? Vibe",
    "? Vibe",
)

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
