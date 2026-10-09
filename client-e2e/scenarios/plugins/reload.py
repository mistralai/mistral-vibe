"""/reload-plugins reports the plugins a re-pin added, removed, and updated."""

from __future__ import annotations

from e2e.app_server.plugins import METHODS, catalog, entry, sample_plugins
from e2e.app_server.scenario import Timeline

_BEFORE = sample_plugins()
_AFTER = [
    entry("devtools", "99887766554433221100ffeeddccbbaa"),
    _BEFORE[1],
    entry("review", "1234567890abcdef1234567890abcdef", version="0.3.0"),
]

handshake = {
    "plugin_catalog/read": [catalog(_BEFORE), catalog(_AFTER)],
    "plugin/reload": {},
}
request_methods = METHODS

timeline: Timeline = ["/reload-plugins\r"]

screen_contains = {"rust": ("Plugins reloaded", "no longer installed")}
