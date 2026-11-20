"""/reload-plugins during a turn says the reload waits for the turn, not that nothing changed."""

from __future__ import annotations

from e2e.app_server.plugins import METHODS, catalog, sample_plugins
from e2e.app_server.scenario import Timeline

handshake = {
    "plugin_catalog/read": catalog(sample_plugins()),
    "plugin/reload": {"deferred": True},
}
request_methods = METHODS

timeline: Timeline = ["/reload-plugins\r"]

screen_contains = ("Plugins will reload when the current turn ends.",)
screen_excludes = ("Nothing changed",)
