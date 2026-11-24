"""Arrows pick a folder in the `/cd` completion, and Enter moves into the one picked.

`alpha/` is listed first and highlighted; Down picks `beta/`, and the golden
requests pin the `session/relocate` to `beta`, not to the folder typed.
"""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.cd._dirs import CLIENT_ARGS, REQUEST_METHODS, handshake

handshake = handshake("beta")
client_args = CLIENT_ARGS
request_methods = REQUEST_METHODS
screen_contains = ("/test/workdir/beta",)

timeline: Timeline = ["/cd ", "\x1b[B", take_snapshot("picked"), "\r"]
