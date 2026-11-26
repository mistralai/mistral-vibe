"""Owner-only remediation for session log artifacts created with permissive modes.

Artifacts created before owner-only creation modes stay readable by other
local users until their modes are tightened. The sweep strips group and other
bits from the session log root and everything under it, preserving owner bits
so plugin scripts keep their owner-execute bit. It never creates the root,
never follows symlinks, and never fails startup: per-path permission errors
are logged and skipped.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
from threading import Lock, Thread

from vibe.core.config import SessionLoggingConfig
from vibe.core.utils import is_windows
from vibe.observability.logging import logger

_GROUP_OTHER = 0o077
_START_LOCK = Lock()
_STARTED = False


def start_restrict_session_log_permissions(
    session_config: SessionLoggingConfig,
) -> None:
    """Start the sweep in a daemon thread, once per process.

    The session-config build calls this for every session, and the sweep is
    idempotent, so the first caller wins and the rest return immediately.
    """
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        _STARTED = True
    Thread(
        target=restrict_session_log_permissions_entrypoint,
        args=(session_config,),
        daemon=True,
        name="restrict_session_log_permissions",
    ).start()


def restrict_session_log_permissions_entrypoint(
    session_config: SessionLoggingConfig,
) -> None:
    """Sweep the session log root and log a summary when paths were tightened."""
    tightened = restrict_session_log_permissions(session_config)
    if tightened:
        logger.debug("Restricted %d session log path(s) to owner-only", tightened)


def restrict_session_log_permissions(session_config: SessionLoggingConfig) -> int:
    """Strip group/other bits from the session log root and everything under it.

    The sweep runs even when session logging is disabled: it remediates
    existing data and writes nothing. Unified session directories are the one
    exception to "everything": only the directories themselves are swept,
    because the store creates them owner-only, so their interior modes cannot
    grant access and skipping the interiors keeps the walk cheap.

    Returns the number of paths tightened. On Windows mode bits do not govern
    access, so the sweep is a no-op there.
    """
    if is_windows():
        return 0
    root = Path(session_config.save_dir)
    if not root.is_dir():
        # The sweep remediates existing logs; it never creates the log root.
        return 0
    tightened = _strip_group_other(root)
    unified_root = root / "unified"
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Symlinked directories are pruned so the walk never reaches through
        # a link out of the log root.
        dirnames[:] = [
            name for name in dirnames if not Path(dirpath, name).is_symlink()
        ]
        current = Path(dirpath)
        if current != unified_root and unified_root in current.parents:
            # Owner-only session directories gate these interiors.
            dirnames[:] = []
        tightened += _strip_group_other(current)
        for name in filenames:
            tightened += _strip_group_other(Path(dirpath, name))
    return tightened


def _strip_group_other(path: Path) -> int:
    """Tighten one path when it carries group/other bits. Returns whether it changed."""
    try:
        info = os.lstat(path)
        # Symlinks report permissive modes but chmod follows them, so a link
        # must never reach the chmod below.
        if stat.S_ISLNK(info.st_mode):
            return 0
        mode = stat.S_IMODE(info.st_mode)
        if not mode & _GROUP_OTHER:
            return 0
        os.chmod(path, mode & ~_GROUP_OTHER)
        return 1
    except OSError as e:
        logger.debug("Session log sweep skipped path=%s err=%s", path, e)
        return 0
