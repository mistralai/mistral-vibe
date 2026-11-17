"""/trusted-folders lists both sections, untrusts a folder with Enter and forgets one with f."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from e2e.app_server.trusted_folders import METHODS, folders

handshake = {
    "workspace/trust/folders/list": folders(
        ["/srv/repo", "/srv/work/api"], ["/srv/scratch"]
    ),
    "workspace/trust/folders/update": [
        folders(["/srv/work/api"], ["/srv/scratch", "/srv/repo"]),
        folders(["/srv/work/api"], ["/srv/repo"]),
    ],
}
request_methods = METHODS | {"telemetry/record"}

timeline: Timeline = [
    "/trusted-folders\r",
    take_snapshot("open"),
    # Enter untrusts /srv/repo; the highlight stays in place, on /srv/work/api.
    "\r",
    "j",
    "f",
    take_snapshot("updated"),
]

screen_contains = (
    "Untrusted /srv/repo. Its project config is ignored the next time Vibe starts.",
    "Forgot the decision for /srv/scratch.",
    # The highlight ends on the untrusted /srv/repo, so Enter would trust it.
    "Enter trust  f forget",
)
