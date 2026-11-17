"""The add view lists the typed folder then its children; ↓ and Enter trust the highlighted child."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from e2e.app_server.trusted_folders import METHODS, candidate, folders, inspection

_WORKDIR = inspection(
    candidate("/test/workdir"),
    candidate("/test/workdir/api"),
    candidate("/test/workdir/web", untrusted_inside=["/test/workdir/web/vendor"]),
)

handshake = {
    "workspace/trust/folders/list": folders(["/srv/repo"], []),
    # The field opens on the checkout, then Ctrl+U empties it before the paste.
    "workspace/trust/folders/inspect": [
        inspection(candidate("/test/workdir")),
        inspection(candidate("/test/workdir")),
        _WORKDIR,
    ],
    "workspace/trust/folders/add": folders(["/srv/repo", "/test/workdir/web"], []),
}
request_methods = METHODS | {"telemetry/record"}

_CLEAR = "\x15"
_PASTE = "\x1b[200~/test/workdir/\x1b[201~"
_DOWN = "\x1b[B"

timeline: Timeline = [
    "/trusted-folders\r",
    "a",
    _CLEAR,
    _PASTE,
    take_snapshot("candidates"),
    _DOWN,
    _DOWN,
    take_snapshot("highlighted"),
    "\r",
]

screen_contains = (
    "Trusted /test/workdir/web and the folders inside it.",
    "Trusted folders · 2 stored",
)
