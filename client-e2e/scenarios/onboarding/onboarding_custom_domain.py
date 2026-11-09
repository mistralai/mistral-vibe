"""`--setup` wizard: the custom-domain screen seeds, tabs, and pastes."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

client_args = ("--setup",)
request_methods = {"setup/status"}
timeline: Timeline = [
    "\r",  # welcome -> theme
    "\r",  # theme -> auth method
    "\r",  # auth: Launch browser -> sign-in target
    "\x1b[B\r",  # target: Other -> custom domain
    "my.example",  # type a domain: the submit hint appears
    "\x15",  # Ctrl+U clears the line, like the chat composer
    "my.example",  # retype: the cleared input hints again
    "\t",  # Tab to the API base: the hint persists (change-driven feedback)
    paste("https://api.my.example\n"),  # paste strips the trailing newline
    "\t",  # Tab again: focus wraps back to the domain input
]
