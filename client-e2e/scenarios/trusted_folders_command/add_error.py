"""A path the server cannot inspect shows its reason, lists no folder, and Enter does nothing."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.trusted_folders import METHODS, folders

handshake = {
    "workspace/trust/folders/list": folders(["/srv/repo"], []),
    "workspace/trust/folders/inspect": {
        "error": {"code": "invalid_params", "message": "Not a usable path: ~nobody/x"}
    },
}
request_methods = METHODS

timeline: Timeline = ["/trusted-folders\r", "a", "\r"]

screen_contains = ("Not a usable path: ~nobody/x", "Esc back")
screen_excludes = ("Enter trust",)
