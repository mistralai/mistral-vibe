"""How many times a process started recovering a Session's interrupted Turn.

A Turn whose recovery itself kills the process would otherwise be offered for
resume on every launch. A recovery that carries the Turn on counts one before
it drives anything. The count is kept beside the store, not in it, so no reader
of the store sees it. A Session that closes cleanly survived its recovery, so
its close resets it. Failing to count or reset is logged, never raised.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from mistralai_vibe_local_harness.vibe._storage import StoredSession, durable_replace

logger = logging.getLogger(__name__)

MAX_RECOVERY_ATTEMPTS = 2
_FILE_NAME = "recovery-attempts.json"


def interrupted_turn_id(stored: StoredSession) -> str | None:
    """The Turn a stopped process left running, which a resume carries on."""
    turn = stored.projection_state.snapshot.latest_turn
    return turn.id if turn is not None and turn.status == "in_progress" else None


def recovery_attempts(session_root: Path, turn_id: str) -> int:
    try:
        document = json.loads((session_root / _FILE_NAME).read_bytes())
    except FileNotFoundError:
        return 0
    except (OSError, ValueError):
        logger.warning("Unreadable recovery attempt count", exc_info=True)
        return 0
    if not isinstance(document, dict) or document.get("turnId") != turn_id:
        return 0
    attempts = document.get("attempts")
    return attempts if isinstance(attempts, int) and attempts > 0 else 0


def record_recovery_attempt(session_root: Path, turn_id: str) -> None:
    """Durably, since the recovery that follows may kill the process."""
    attempts = recovery_attempts(session_root, turn_id) + 1
    path = session_root / _FILE_NAME
    temporary = path.with_name(f".{_FILE_NAME}.tmp")
    with temporary.open("wb") as file:
        file.write(json.dumps({"turnId": turn_id, "attempts": attempts}).encode())
        file.flush()
        os.fsync(file.fileno())
    durable_replace(temporary, path)


def count_recovery_attempt(session_root: Path, turn_id: str) -> None:
    """Count one, or log why not: the count must never stop a recovery."""
    try:
        record_recovery_attempt(session_root, turn_id)
    except OSError:
        logger.warning("Could not count a recovery attempt", exc_info=True)


def reset_recovery_attempts(session_root: Path) -> None:
    try:
        (session_root / _FILE_NAME).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not reset the recovery attempt count", exc_info=True)
