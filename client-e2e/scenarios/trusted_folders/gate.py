from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

handshake = {
    "workspace/trust/status": {
        "status": "untrusted",
        "details": {
            "cwd": "/home/user/untrusted",
            "repoRoot": None,
            "detectedFiles": ["AGENTS.md", ".vibe/"],
            "repoDetectedFiles": [],
            "repoExplicitlyUntrusted": False,
            "settingsPath": "/home/user/.vibe/trusted_folders.toml",
            "availableDecisions": ["trust_cwd", "decline"],
        },
    }
}

# Startup shows the gate; Enter takes "Trust folder" and opens the session.
timeline: Timeline = ["\r", take_snapshot()]
