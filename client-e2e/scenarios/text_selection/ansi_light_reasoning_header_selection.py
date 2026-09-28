"""ANSI-light selection over a thought disclosure header uses the ALABASTER blend."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    reasoning,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0", "VIBE_THEME": "ansi-light"}
handshake = {
    "config/read": {"config": {"showThinkingNodes": True, "theme": "ansi-light"}},
    "runtime/read": {
        "runtime": {"config": {"showThinkingNodes": True, "theme": "ansi-light"}}
    },
}

_HEADER_DRAG = "\x1b[<0;3;30M\x1b[<32;8;30M\x1b[<0;8;30m"

timeline: Timeline = [
    "think about it\r",
    turn_started(),
    user_msg("think about it"),
    reasoning("This text is only visible after expanding the thought."),
    assistant_msg("Done."),
    turn_completed(),
    _HEADER_DRAG,
]
