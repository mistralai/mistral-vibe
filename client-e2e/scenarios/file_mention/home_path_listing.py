"""An `@~/` path lists the home directory's entries, directories with a trailing `/`."""

from __future__ import annotations

from e2e.app_server.config import WORKSPACE_DIR
from e2e.app_server.scenario import Timeline

env = {"HOME": str(WORKSPACE_DIR)}

timeline: Timeline = ["@~/"]

screen_contains = ("~/at_popup/", "~/data/", "~/sub/", "~/README.md")
