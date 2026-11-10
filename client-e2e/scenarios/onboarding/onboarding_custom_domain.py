"""`--setup` wizard: the custom-domain screen seeds, tabs, and pastes."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline, take_snapshot

client_args = ("--setup",)
request_methods = {"setup/status"}
timeline: Timeline = [
    "\r",
    take_snapshot(),  # welcome -> theme
    "\r",
    take_snapshot(),  # theme -> auth method
    "\r",
    take_snapshot(),  # auth: Launch browser -> sign-in target
    "\x1b[B\r",
    take_snapshot(),  # target: Other -> custom domain
    "my.example",
    take_snapshot(),  # type a domain: the submit hint appears
    "\x15",
    take_snapshot(),  # Ctrl+U clears the line, like the chat composer
    "my.example",
    take_snapshot(),  # retype: the cleared input hints again
    "\t",
    take_snapshot(),  # Tab to the API base: the hint persists (change-driven feedback)
    paste("https://api.my.example\n"),
    take_snapshot(),  # paste strips the trailing newline
    "\t",
    take_snapshot(),  # Tab again: focus wraps back to the domain input
]
