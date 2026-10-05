"""An accepted `/` mention is colored, the caret jumps over it, and Backspace deletes it whole."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SKILLS = [
    {
        "name": "lint",
        "description": "Run the linters.",
        "prompt": "",
        "userInvocable": True,
        "source": "local",
    }
]
# Composer row 36, text from column 3: column 9 is the `i` of `/lint`.
_CLICK_INSIDE = "\x1b[<0;9;36M\x1b[<0;9;36m"
_LEFT = "\x1b[D"
_RIGHT = "\x1b[C"
_BACKSPACE = "\x7f"

handshake = {"runtime/read": {"runtime": {"skills": _SKILLS}}}

capture_startup = False
capture_steps = {1, 2, 3, 4}
settle_per_key = True
expected_clipboard = "run done "

# The click parks the caret after the mention, so one Left lands before its `/`.
timeline: Timeline = [
    "run /li",
    "\t",
    _CLICK_INSIDE + _LEFT,
    _RIGHT + _BACKSPACE,
    "done\x1b[18~\x19",
]
