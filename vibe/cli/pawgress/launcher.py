from __future__ import annotations

import subprocess
import sys

from vibe.core.logger import logger


def spawn_overlay_candidate() -> None:
    """Start an overlay candidate process.

    The candidate self-elects via flock (see ``vibe.overlay.singleton``); if
    another overlay already holds the lock the candidate exits immediately.
    Safe to call from multiple sessions concurrently.
    """
    try:
        subprocess.Popen(
            [sys.executable, "-m", "vibe.overlay"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        logger.warning("Failed to spawn pawgress overlay: %s", e)
