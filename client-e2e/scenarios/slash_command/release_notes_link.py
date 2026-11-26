"""A link in the notes of `/release-notes` opens on click, below a blank row that sets it apart."""

from __future__ import annotations

import os

from e2e.app_server.config import WHATS_NEW_LINK_FIXTURE
from e2e.app_server.scenario import Action, Timeline, take_snapshot

env = {"VIBE_WHATS_NEW_DIR": os.fspath(WHATS_NEW_LINK_FIXTURE)}

_URL = "https://example.test/desktop"
# `here` spans one-based columns 41-44 on row 9, a blank row below the first list.
_CLICK = "\x1b[<0;42;9M\x1b[<0;42;9m"

timeline: Timeline = ["/release-notes\r", take_snapshot(), _CLICK]

screen_contains = ("Vibe desktop app: get it here",)
expected_actions = [Action("open_url", _URL)]
