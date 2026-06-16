"""Gate: block git commit/push/rebase when diff is large or touches critical paths."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from .bypass import is_bypassed

_TRIGGERS = ("git commit", "git push", "git rebase")

_CRITICAL = re.compile(
    r"auth|permission|token|credential|secret|migration|alembic|\.sql$"
    r"|routes|endpoint|schema|\.env(\.|$)",
    re.IGNORECASE,
)


def evaluate(command: str, cwd: str) -> dict | None:
    if not any(t in command for t in _TRIGGERS):
        return None

    if is_bypassed(command, ("temper:skip",)):
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

    try:
        numstat = subprocess.run(
            ["git", "diff", "--numstat", "--cached"],
            capture_output=True, text=True, cwd=cwd, timeout=10,
        )
        names = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, cwd=cwd, timeout=10,
        )
        full_diff = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True, text=True, cwd=cwd, timeout=15,
        )
    except Exception:
        return None

    # Clear if /temper skill already approved this exact staged diff.
    # The skill writes SHA256(git diff --cached) to .vibe/.temper_ok so that
    # a subsequent `git add` (which changes the hash) invalidates the approval.
    temper_ok = repo_root / ".vibe" / ".temper_ok"
    if temper_ok.exists():
        current_hash = hashlib.sha256(full_diff.stdout.encode()).hexdigest()
        try:
            if temper_ok.read_text(encoding="utf-8").strip() == current_hash:
                return None
        except Exception:
            pass

    files_changed = 0
    total_lines = 0
    for line in numstat.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            added = int(parts[0]) if parts[0] != "-" else 0
            removed = int(parts[1]) if parts[1] != "-" else 0
            total_lines += added + removed
            files_changed += 1
        except ValueError:
            pass

    changed_files = [f for f in names.stdout.strip().splitlines() if f]
    is_critical = any(_CRITICAL.search(f) for f in changed_files)

    if files_changed <= 10 and total_lines <= 200 and not is_critical:
        return None

    reasons: list[str] = []
    if files_changed > 10:
        reasons.append(f"{files_changed} files changed")
    if total_lines > 200:
        reasons.append(f"{total_lines} lines changed")
    if is_critical:
        reasons.append("critical path files (auth/migrations/schema/secrets)")

    return {
        "decision": "deny",
        "reason": (
            f"🔴 temper: Large or critical diff — {', '.join(reasons)}.\n\n"
            f"Run /temper before committing.\n\n"
            f"To bypass: append # temper:skip"
        ),
    }
