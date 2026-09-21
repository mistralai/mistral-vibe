"""An expanded body folds an unbreakable token inside its border."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "print the binary path"

_COMMAND = "cat run.py"

# One token with no break opportunity, far wider than the bordered body: it must
# fold inside the border, never overflow onto a borderless row at column 0.
_STDOUT = (
    'BIN = "/private/var/folders/kk/qqyqm2p9057f1qz305zlz3qr0000gn/T/'
    "vibe-scratchpad-ff5f5f95-nl1b7xuo/deeply/nested/checkout/vibe/cli-rust/"
    'target/release/vibe-rs"\n'
    "print(BIN)"
)

_CLICK = "\x1b[<0;1;30M\x1b[<0;1;30m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash(_COMMAND, _STDOUT),
    assistant_msg("Done."),
    turn_completed(),
    _CLICK,
]
