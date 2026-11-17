"""Tab writes the highlighted folder into the field, which then lists that folder's children."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.trusted_folders import METHODS, candidate, folders, inspection

handshake = {
    "workspace/trust/folders/list": folders(["/srv/repo"], []),
    "workspace/trust/folders/inspect": [
        inspection(candidate("/test/workdir")),
        inspection(candidate("/test/workdir")),
        inspection(candidate("/test/workdir"), candidate("/test/workdir/web")),
        inspection(candidate("/test/workdir/web"), candidate("/test/workdir/web/src")),
    ],
}
request_methods = METHODS

timeline: Timeline = [
    "/trusted-folders\r",
    "a",
    "\x15",
    "\x1b[200~/test/workdir/\x1b[201~",
    "\x1b[B",
    "\t",
]

screen_contains = ("/test/workdir/web/", "/test/workdir/web/src")
