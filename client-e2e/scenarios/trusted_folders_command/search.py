"""/trusted-folders filters both sections as the query is typed."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.trusted_folders import METHODS, folders

handshake = {
    "workspace/trust/folders/list": folders(
        ["/srv/repo", "/srv/work/api"], ["/srv/scratch"]
    )
}
request_methods = METHODS

timeline: Timeline = ["/trusted-folders\r", "/", "scr"]

screen_contains = ("/srv/scratch", "No folders match this filter", "Esc leave search")
screen_excludes = ("/srv/repo", "/srv/work/api")
