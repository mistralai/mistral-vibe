"""Singleton election + well-known paths for the overlay broker.

A single overlay process owns the Unix socket. Election is a non-blocking
``flock`` held for the overlay's whole lifetime — the kernel releases it on
death, so stale cleanup is unambiguous.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from vibe.core.paths import VIBE_HOME


def pawgress_dir() -> Path:
    path = VIBE_HOME.path / "pawgress"
    path.mkdir(parents=True, exist_ok=True)
    return path


def socket_path() -> Path:
    return pawgress_dir() / "overlay.sock"


def lock_path() -> Path:
    return pawgress_dir() / "overlay.lock"


def acquire_overlay_lock() -> int | None:
    """Non-blocking exclusive flock. Returns the held fd, or None if taken.

    The caller must keep the fd open for the overlay's whole lifetime.
    """
    fd = os.open(str(lock_path()), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd
