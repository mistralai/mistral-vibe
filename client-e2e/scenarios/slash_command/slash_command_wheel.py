from __future__ import annotations

from e2e.app_server.scenario import Timeline

_WHEEL_DOWN = "\x1b[<65;60;26M"
_WHEEL_UP = "\x1b[<64;60;26M"

# Rust keeps backticks in the /connectors menu description; python strips them.
skip_terminal_parity = (
    "rust keeps backticks in the /connectors menu description; python strips them"
)

timeline: Timeline = ["/", _WHEEL_DOWN * 3, _WHEEL_UP, _WHEEL_UP * 5]
