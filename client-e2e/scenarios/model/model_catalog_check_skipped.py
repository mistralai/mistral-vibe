"""Opening /model checks nothing when the catalog locks no model."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

request_methods = {"config/read"}
screen_excludes = ("Checking for new models",)

timeline: Timeline = ["/model", "\r"]
