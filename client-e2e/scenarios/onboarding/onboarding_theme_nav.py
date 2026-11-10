"""`--setup` wizard: theme screen live preview and wrap-around navigation."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

client_args = ("--setup",)
request_methods = {"setup/status"}
# Up to textual-light, Down back to ansi-dark, then nine Ups from ansi-dark:
# eight reach `auto` and the ninth wraps to tokyo-night.
timeline: Timeline = [
    "\r",
    take_snapshot(),
    "\x1b[A",
    take_snapshot(),
    "\x1b[B",
    take_snapshot(),
    "\x1b[A" * 9,
    take_snapshot(),
]
