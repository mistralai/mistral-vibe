"""File completion remains available with live indexing enabled."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "runtime/read": {
        "runtime": {
            "config": {
                "enableFileAutocomplete": True,
                "fileWatcherForAutocomplete": True,
            }
        }
    }
}

timeline: Timeline = ["@at_popup/", "al"]
screen_contains = ("alpha.md",)
screen_excludes = ("beta.md", "gamma.md")
