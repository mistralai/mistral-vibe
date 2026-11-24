"""Enter on a partial `/cd` path moves into the folder highlighted, not the text typed.

`al` names no folder; `alpha/` is the only match, highlighted, and the golden
requests pin the `session/relocate` to `alpha`.
"""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.cd._dirs import CLIENT_ARGS, REQUEST_METHODS, handshake, moved

handshake = handshake()
client_args = CLIENT_ARGS
request_methods = REQUEST_METHODS
on_request = moved()
screen_contains = ("Moved from", "/test/workdir/alpha")
screen_excludes = ("Not a directory",)

timeline: Timeline = ["/cd al", "\r"]
