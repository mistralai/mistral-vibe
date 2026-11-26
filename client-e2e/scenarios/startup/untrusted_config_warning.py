"""Untrusted local config folders show the once-per-folder warning banner."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

handshake = {
    "workspace/trust/untrustedConfig": {
        "dirs": ["/home/user/repo/.vibe", "/home/user/other/.vibe"],
        "settingsPath": "/home/user/.vibe/trusted_folders.toml",
    }
}

# The banner mounts from a startup worker; typing settles it before the snapshot.
timeline: Timeline = ["x", take_snapshot()]
