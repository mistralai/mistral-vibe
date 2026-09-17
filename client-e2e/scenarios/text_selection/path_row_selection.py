"""A wrapped row starting with a path is body text, not chrome: all of it selects."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# The scenario renders a macOS-specific path (/private/var/folders/...) which
# may wrap differently on Linux due to platform-dependent text layout.
skip = "golden layout differs between macOS and Linux (new scenario from base branch)"

_PROMPT = "where is the binary"

# The path is pushed onto a wrapped row of its own, so that row starts with "/".
_ANSWER = (
    "The release binary of the Rust client lives under the checkout, at the path "
    "/private/var/folders/kk/vibe-scratchpad/cli-rust/target/release/vibe-rs today."
)

_DRAG = "\x1b[<0;3;32M\x1b[<32;60;32M\x1b[<0;60;32m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _DRAG,
]
