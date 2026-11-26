"""A scratch folder is offered with why it cannot be trusted, and Enter leaves it alone."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.trusted_folders import METHODS, candidate, folders, inspection

handshake = {
    "workspace/trust/folders/list": folders(["/srv/repo"], []),
    "workspace/trust/folders/inspect": [
        inspection(candidate("/test/workdir")),
        inspection(candidate("/test/workdir")),
        inspection(candidate("/home/user/.vibe/scratch/1", problem="scratch")),
    ],
}
request_methods = METHODS

timeline: Timeline = [
    "/trusted-folders\r",
    "a",
    "\x15",
    "\x1b[200~/home/user/.vibe/scratch/1/\x1b[201~",
    "\r",
]

screen_contains = (
    "/home/user/.vibe/scratch/1  scratch folder",
    "A scratch folder cannot be trusted",
    "Tab complete  Esc back",
)
screen_excludes = ("Enter trust",)
