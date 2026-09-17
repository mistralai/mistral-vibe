from __future__ import annotations

from e2e.app_server.scenario import Timeline

_DOWN = "\x1b[B"
_UP = "\x1b[A"

# Rust keeps backticks in the /connectors menu description; python strips them.
skip_terminal_parity = (
    "rust keeps backticks in the /connectors menu description; python strips them"
)

timeline: Timeline = ["/", _DOWN, _DOWN * 10, _UP * 11, _UP]
