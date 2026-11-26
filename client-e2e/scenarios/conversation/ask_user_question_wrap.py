"""An `ask_user_question` whose title, option and footer are wider than the box."""

from __future__ import annotations

from e2e.app_server.events import (
    ask_user_question,
    ask_user_question_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, take_snapshot

env = {"VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}

_DOWN = "\x1b[B"
_UP = "\x1b[A"

_QUESTIONS = [
    {
        "question": (
            "Force-push (--force-with-lease) the rebased branch "
            "`paulvezia/vibe-4478-rust-cli-file-logging-and-log-level` to origin "
            "so the open pull request picks up the rewritten commits?"
        ),
        "options": [
            {
                "label": "Force-push with lease",
                "description": (
                    "Overwrite the remote branch only if nobody else has pushed to it "
                    "since the last fetch, which keeps the review history linear"
                ),
            },
            {"label": "Cancel"},
        ],
    }
]
_FOOTER = (
    "This rewrites the remote history of a branch that already has an open pull "
    "request, so anyone who checked it out will need to reset their local copy."
)

timeline: Timeline = [
    "force push\r",
    turn_started(),
    user_msg("force push"),
    ask_user_question_added(_QUESTIONS, footer_note=_FOOTER),
    ask_user_question(_QUESTIONS, footer_note=_FOOTER),
    {"release": 5},
    _DOWN,
    _UP,
    take_snapshot(),
]
