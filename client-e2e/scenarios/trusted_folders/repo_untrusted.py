from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "workspace/trust/status": {
        "status": "untrusted",
        "details": {
            "cwd": "/home/user/repo/app",
            "repoRoot": "/home/user/repo",
            "detectedFiles": ["AGENTS.md"],
            "repoDetectedFiles": [],
            "repoExplicitlyUntrusted": True,
            "settingsPath": "/home/user/.vibe/trusted_folders.toml",
            "availableDecisions": ["trust_cwd", "decline"],
        },
    }
}

# An explicitly untrusted repo replaces the repo line with its warning and
# drops the repo option; "2" declines without touching the selection.
timeline: Timeline = ["2"]
