"""Tab on a directory lists it first, preselected, then its entries; typing a name drops its row."""

from __future__ import annotations

from e2e.app_server.config import WORKSPACE_DIR
from e2e.app_server.scenario import Timeline, take_snapshot

env = {"HOME": str(WORKSPACE_DIR)}

timeline: Timeline = ["@~/at_p", "\t", take_snapshot(), "b"]

screen_contains = ("~/at_popup/beta.md",)
screen_excludes = ("~/at_popup/alpha.md", "~/at_popup/gamma.md")
