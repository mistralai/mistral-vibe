from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import subprocess


async def repository_fingerprint(cwd: Path) -> str | None:
    return await asyncio.to_thread(_repository_fingerprint_sync, cwd)


def _repository_fingerprint_sync(cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z"],
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return hashlib.sha256(result.stdout).hexdigest()
