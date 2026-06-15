"""Gate: block git commit/push when plan files are newer than CRITIQUE.md."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .bypass import is_bypassed

_TRIGGERS = ("git commit", "git push")


def evaluate(command: str, cwd: str) -> dict | None:
    if not any(t in command for t in _TRIGGERS):
        return None

    if is_bypassed(command, ("whetstone:skip",)):
        return None

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        )
        if result.returncode != 0:
            return None
        repo_root = Path(result.stdout.strip())
    except Exception:
        return None

    plans_dir = repo_root / ".claude" / "plans"
    if not plans_dir.exists():
        return None

    plan_files = [f for f in plans_dir.glob("*.md") if f.name != "CRITIQUE.md"]
    if not plan_files:
        return None

    critique = plans_dir / "CRITIQUE.md"
    if not critique.exists():
        return _deny(
            "Plans exist but no /autocritic critique found.\n\n"
            "Run /autocritic before committing."
        )

    newest = max(plan_files, key=lambda f: f.stat().st_mtime)
    if newest.stat().st_mtime > critique.stat().st_mtime:
        return _deny(
            f"Plan '{newest.name}' was updated after the last /autocritic critique.\n\n"
            "Run /autocritic on the updated plan before committing."
        )

    return None


def _deny(detail: str) -> dict:
    return {
        "decision": "deny",
        "reason": f"🔴 whetstone: {detail}\n\nTo bypass: append # whetstone:skip",
    }
