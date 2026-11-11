"""`/data-retention` link under the `/release-notes` modal is no terminal hyperlink, like its click."""

from __future__ import annotations

import os

from e2e.app_server.config import WHATS_NEW_HISTORY_FIXTURE
from e2e.app_server.scenario import Timeline

env = {"VIBE_WHATS_NEW_DIR": os.fspath(WHATS_NEW_HISTORY_FIXTURE)}

timeline: Timeline = ["/data-retention\r", "/release-notes\r"]

screen_contains = ("Release notes",)
screen_hyperlinks = ()
