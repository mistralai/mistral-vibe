from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"VIBE_TEST_TRUST_GRACE_MS": "3600000"}

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

# Enter and digits pressed during the grace period leave the gate open.
timeline: Timeline = ["\r", "1"]
screen_contains = ("Trust this folder?",)
