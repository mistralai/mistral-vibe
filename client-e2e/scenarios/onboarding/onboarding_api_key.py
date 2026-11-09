"""`--setup` wizard: the API key screen types, and clicks focus its input."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--setup",)
request_methods = {"setup/status"}

# SGR coordinates are 1-based. The masked card's value row is row 22 once
# the validation feedback appears; clicking four bullets in moves the caret
# from the end of the value to the clicked position (Textual `Input` click).
_CLICK_INTO_INPUT = "\x1b[<0;32;22M\x1b[<0;32;22m"

timeline: Timeline = [
    "\r",  # welcome -> theme
    "\r",  # theme -> auth method
    "\x1b[B\r",  # auth: Use an API key -> api key screen
    "test-key-123",  # the masked card fills and validates
    _CLICK_INTO_INPUT,
]
