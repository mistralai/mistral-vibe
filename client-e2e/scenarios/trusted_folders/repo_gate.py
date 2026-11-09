from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "workspace/trust/status": {
        "status": "untrusted",
        "details": {
            "cwd": "/home/user/repo/packages/app",
            "repoRoot": "/home/user/repo",
            "detectedFiles": ["AGENTS.md"],
            "repoDetectedFiles": [".vibe/", "packages/AGENTS.md"],
            "repoExplicitlyUntrusted": False,
            "settingsPath": "/home/user/.vibe/trusted_folders.toml",
            "availableDecisions": ["trust_repo", "trust_cwd", "decline"],
        },
    }
}

# Three options and the repo title; Left wraps to the end, Right comes back,
# then "1" selects "Trust full repo" straight away.
timeline: Timeline = ["\x1b[D", "\x1b[C", "1"]
