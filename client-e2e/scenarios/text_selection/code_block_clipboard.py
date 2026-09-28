"""Code-block selection preserves source indentation and blank lines."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}
expected_clipboard = """\
fn main() {
    let value = 1;

    if value > 0 {
        println!("{value}");
    }
}"""

_PROMPT = "show indented code"
_ANSWER = f"```\n{expected_clipboard}\n```"
_SELECT_CODE = "\x1b[<0;3;26M\x1b[<32;119;32M\x1b[<0;119;32m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _SELECT_CODE,
]
