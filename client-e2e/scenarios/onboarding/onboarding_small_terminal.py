"""`--setup` in a small terminal keeps every wizard screen visible (VIBE-5760)."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize, take_snapshot

client_args = ("--setup",)
request_methods = {"setup/status"}

timeline: Timeline = [
    "\r",  # welcome -> theme
    resize(12, 100),
    take_snapshot(),  # compact theme screen: title and list, no preview
    "\r",  # theme -> auth method
    take_snapshot(),  # the sign-in option cards fit at 12 rows
    resize(6, 100),
    take_snapshot(),  # below the smallest panel: the enlarge notice
]

screen_contains = ("Enlarge terminal to continue",)
