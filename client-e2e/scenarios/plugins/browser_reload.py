"""`r` in the open browser reloads, reports the change, and re-renders the list."""

from __future__ import annotations

from e2e.app_server.plugins import METHODS, catalog, entry, sample_plugins
from e2e.app_server.scenario import Timeline

_PINNED = sample_plugins()
_RELOADED = [*_PINNED[:2], entry("review", "1234567890abcdef1234567890abcdef")]

handshake = {
    "plugin_catalog/read": [catalog(_PINNED), catalog(_PINNED), catalog(_RELOADED)],
    "plugin/reload": {},
}
request_methods = METHODS

timeline: Timeline = ["/plugins\r", "r"]

screen_contains = {"rust": ("Plugins reloaded", "review")}
