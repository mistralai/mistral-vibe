"""One click on a plugin row opens its detail, like Enter."""

from __future__ import annotations

from e2e.app_server.plugins import METHODS, catalog, dropped, sample_plugins
from e2e.app_server.scenario import Timeline

_BROKEN = dropped(
    "/opt/vibe/plugins/broken/plugin.json", "missing required field 'name'"
)
handshake = {"plugin_catalog/read": catalog(sample_plugins(), [_BROKEN])}
request_methods = METHODS

# Press and release on the docs row, in 1-based terminal coordinates.
_CLICK_DOCS = "\x1b[<0;10;32M\x1b[<0;10;32m"

timeline: Timeline = ["/plugins\r", _CLICK_DOCS]
