"""Enter without edits still saves, with no changes, like Python."""

from __future__ import annotations

from e2e.app_server.proxy_setup import METHODS, prefilled
from e2e.app_server.scenario import Timeline

handshake = prefilled()
request_methods = METHODS
screen_contains = {
    "rust": ("Proxy settings saved. Restart the CLI for changes to take effect.",)
}
timeline: Timeline = ["/proxy-setup\r", "\r"]
