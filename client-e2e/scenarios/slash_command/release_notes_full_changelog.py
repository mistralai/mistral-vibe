"""`/release-notes` links the full public changelog; Enter and a click on the link both open it."""

from __future__ import annotations

import os

from e2e.app_server.config import WHATS_NEW_HISTORY_FIXTURE
from e2e.app_server.scenario import Action, Timeline

env = {"VIBE_WHATS_NEW_DIR": os.fspath(WHATS_NEW_HISTORY_FIXTURE)}

_URL = "https://github.com/mistralai/mistral-vibe/blob/main/CHANGELOG.md"
# The link spans one-based columns 20-83 on row 36, a blank row above the footer hints.
_CLICK = "\x1b[<0;40;36M\x1b[<0;40;36m"

timeline: Timeline = ["/release-notes\r", "\r", _CLICK]

screen_contains = (
    "Full changelog: https://github.com/mistralai/mistral-vibe/blob/main/CHANGELOG.md",
)
expected_actions = [Action("open_url", _URL), Action("open_url", _URL)]
