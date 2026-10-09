"""A transcript drag over rows a hidden tall composer used to cover selects the transcript."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    paste,
    tool_approval,
    tool_approval_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {
    "VIBE_INPUT_GRACE_PERIOD_MS": "0",
    "VIBE_REPLAY_SETTLE_BUSY": "1",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
    "SSH_TTY": "/dev/pts/0",
}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
clipboard_clients = {"rust"}
clipboard_contains = ("answer line 25",)

_SHIFT_ENTER = "\x1b[13;2u"
_ANSWER = " ".join(f"answer line {number}" for number in range(1, 31))
_COMMAND = {"command": "echo hello"}

# Press on a row the 18-line composer covered before the approval hid it, then drag up into the answer.
_DRAG = "\x1b[<0;60;22M\x1b[<32;3;18M\x1b[<0;3;18m"

# Two inline pastes: a paste over ten lines would collapse into a placeholder.
_HALF = "\n".join(f"draft line {number}" for number in range(1, 10))
_DRAFT: Timeline = [paste(_HALF), _SHIFT_ENTER, paste(_HALF)]

# The approval is held back until the draft is typed, then released over it.
timeline: Timeline = [
    "run the command\r",
    turn_started(),
    user_msg("run the command"),
    assistant_msg(_ANSWER),
    tool_approval_added("bash", "shell", _COMMAND),
    tool_approval("bash", "shell", _COMMAND),
    {"release": 4},
    *_DRAFT,
    {"release": 2},
    _DRAG,
]
capture_steps = {len(_DRAFT) + 1, len(_DRAFT) + 2, len(_DRAFT) + 3}

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
