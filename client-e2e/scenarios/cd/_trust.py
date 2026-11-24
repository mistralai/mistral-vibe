"""An untrusted `/cd` target: the trust status the gate opens on."""

from __future__ import annotations

from typing import Any

from scenarios.cd._dirs import ROOT, handshake


def trust_handshake() -> dict[str, Any]:
    """Startup stays trusted; the `/cd` target answers with gate details."""
    return {
        **handshake(),
        "workspace/trust/status": [
            {"status": "trusted", "details": None},
            {
                "status": "untrusted",
                "details": {
                    "cwd": str(ROOT / "alpha"),
                    "repoRoot": None,
                    "detectedFiles": [".vibe/"],
                    "repoDetectedFiles": [],
                    "repoExplicitlyUntrusted": False,
                    "settingsPath": "/home/user/.vibe/trusted_folders.toml",
                    "availableDecisions": ["trust_cwd", "decline"],
                },
            },
        ],
    }
