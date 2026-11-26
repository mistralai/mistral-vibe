"""Esc closes /trusted-folders and gives the input box back."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.trusted_folders import METHODS, folders

handshake = {"workspace/trust/folders/list": folders(["/srv/repo"], [])}
request_methods = METHODS

timeline: Timeline = ["/trusted-folders\r", "\x1b[27u"]

screen_excludes = ("Search folders", "Trusted folders ·")
