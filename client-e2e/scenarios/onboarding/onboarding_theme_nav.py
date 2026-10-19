"""`--setup` wizard: theme screen live preview and wrap-around navigation."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--setup",)
request_methods = {"setup/status"}
# Up to textual-light, Down back to ansi-dark, then eight Ups from ansi-dark:
# seven reach `auto` and the eighth wraps to tokyo-night.
timeline: Timeline = ["\r", "\x1b[A", "\x1b[B", "\x1b[A" * 8]
