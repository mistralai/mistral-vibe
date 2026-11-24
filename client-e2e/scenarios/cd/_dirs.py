"""Shared `/cd` fixtures: real folders under a fixed `/tmp` root and the relocate answer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import SESSION_ID
from e2e.app_server.scenario import AppServerEvent

# A fixed shallow `/tmp` path keeps the goldens checkout-independent.
ROOT = Path("/tmp/vibe_e2e_fixtures/cd")
for _name in ("alpha", "beta", ".hidden"):
    (ROOT / _name).mkdir(parents=True, exist_ok=True)
(ROOT / "notes.md").write_text("notes\n", encoding="utf-8")
# macOS resolves `/tmp` to `/private/tmp`; the client sends canonical paths.
REAL = ROOT.resolve()
# The client starts in `ROOT`, so `/cd` resolves relative paths against it.
CLIENT_ARGS = ("--workdir", str(ROOT))
REQUEST_METHODS = {"session/relocate", "workspace/trust/status", "telemetry/record"}


def _state(cwd: str) -> dict[str, Any]:
    state = json.loads(FIXTURE_PATH.read_text())["handshake"]["session/start"]["state"]
    state["session"]["cwd"] = cwd
    state["session"]["workspaceRoots"] = [cwd]
    return state


def handshake(*targets: str) -> dict[str, Any]:
    """Answer each move, in order, with the session state in `ROOT/<target>`."""
    answers = [
        {"state": _state(str(REAL / target))} for target in targets or ("alpha",)
    ]
    return {"session/relocate": answers}


def moved(target: str = "alpha") -> dict[str, list[AppServerEvent]]:
    """The relocation checkpoint the server appends to the history on a move.

    It names `ROOT`, not `REAL`, so the row reads the same on macOS and Linux.
    """
    cwd = str(ROOT / target)
    entry = {
        "id": f"checkpoint:relocation:{target}",
        "sessionId": SESSION_ID,
        "turnId": None,
        "createdAt": 1_787_593_260_000,
        "updatedAt": 1_787_593_260_000,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "type": "checkpoint",
        "kind": "relocation",
        "message": f"Moved to {cwd}",
        "details": {"cwd": cwd, "previousCwd": str(ROOT)},
    }
    event = {
        "method": "history/entryAdded",
        "params": {
            "sessionId": SESSION_ID,
            "emittedAt": 1_787_593_260_000,
            "turnId": None,
            "entry": entry,
        },
    }
    return {"session/relocate": [event]}
