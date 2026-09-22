from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "workspace/trust/status": {
        "status": "untrusted",
        "details": {
            "cwd": "/home/user/untrusted",
            "repoRoot": "/home/user/untrusted",
            "detectedFiles": [".vibe/"],
            "repoDetectedFiles": [],
            "repoExplicitlyUntrusted": True,
            "settingsPath": "/home/user/.vibe/trusted_folders.toml",
            "availableDecisions": ["trust_cwd", "decline"],
        },
    }
}

# A repo root equal to the cwd hides the repo line; Right then Enter declines,
# and the session still opens.
timeline: Timeline = ["\x1b[C", "\r"]
