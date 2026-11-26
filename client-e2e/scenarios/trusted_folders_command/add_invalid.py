"""A path naming no folder offers its closest existing parent first, and Enter trusts that parent."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.trusted_folders import METHODS, candidate, folders, inspection

handshake = {
    "workspace/trust/folders/list": folders(["/srv/repo"], []),
    "workspace/trust/folders/inspect": [
        inspection(candidate("/test/workdir")),
        inspection(candidate("/test/workdir")),
        inspection(candidate("/srv"), typed_problem="missing"),
    ],
    "workspace/trust/folders/add": folders(["/srv", "/srv/repo"], []),
}
request_methods = METHODS

timeline: Timeline = [
    "/trusted-folders\r",
    "a",
    "\x15",
    "\x1b[200~/srv/nope\x1b[201~",
    "\r",
]

screen_contains = ("Trusted /srv and the folders inside it.",)
