"""The reply, selection and handshake the copy light scenarios share."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

ENV = {"SSH_TTY": "/dev/pts/0", "VIBE_THEME": "mistral-dark"}
COPIED = "The light crosses the copied text"
# Press on the reply's first row, drag to its last, release to copy.
SELECT = "\x1b[<0;1;8M" + "\x1b[<32;120;16M" + "\x1b[<0;120;16m"
REPLY: Timeline = [
    "make it shine\r",
    turn_started(),
    user_msg("make it shine"),
    assistant_msg(
        "The light crosses the copied text from left to right.\n\n"
        "Only the background changes, then it settles back."
    ),
    turn_completed(),
]


def mistral_dark(**config: object) -> dict[str, object]:
    """The mistral-dark handshake, with extra runtime config."""
    return {
        "config/read": {"config": {"theme": "mistral-dark"}},
        "runtime/read": {"runtime": {"config": {"theme": "mistral-dark", **config}}},
    }
