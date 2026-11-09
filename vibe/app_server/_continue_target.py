from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import hashlib
from pathlib import Path
import time

from vibe.observability.logging import logger
from vibe.utils.io import atomic_replace, read_safe_async

POINTERS_DIR = "continue-pointers"
STALE_AFTER_SECONDS = 30 * 24 * 60 * 60
# The `harnessCode` of the error `session/continue` answers when nothing matches.
NOTHING_TO_CONTINUE = "nothing_to_continue"


@dataclass(frozen=True, slots=True)
class TerminalSeat:
    """The last session one terminal used in one folder, for ``--continue``."""

    storage_root: Path
    terminal_key: str
    cwd: str

    @property
    def pointer_path(self) -> Path:
        # Hashed so neither the key nor the folder can shape the path.
        digest = hashlib.sha256(f"{self.terminal_key}\0{self.cwd}".encode()).hexdigest()
        return self.storage_root / POINTERS_DIR / digest

    async def session_id(self) -> str | None:
        try:
            text = (await read_safe_async(self.pointer_path)).text.strip()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning(
                "Failed to read the continue pointer path=%s",
                self.pointer_path,
                exc_info=exc,
            )
            return None
        return text or None

    async def record(self, session_id: str) -> None:
        path = self.pointer_path
        try:
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            await atomic_replace(path, f"{session_id}\n")
            await asyncio.to_thread(
                prune_stale_pointers, path.parent, time.time() - STALE_AFTER_SECONDS
            )
        except OSError as exc:
            logger.warning(
                "Failed to record the continue pointer path=%s", path, exc_info=exc
            )


def prune_stale_pointers(directory: Path, cutoff: float) -> None:
    for entry in directory.iterdir():
        with contextlib.suppress(OSError):
            if entry.stat().st_mtime < cutoff:
                entry.unlink()


async def continue_target(
    seat: TerminalSeat | None, eligible_ids: set[str], latest_id: str | None
) -> str | None:
    if seat is not None and (pointed := await seat.session_id()) in eligible_ids:
        return pointed
    return latest_id
