"""Name the Windows failure where the user's account cannot open the Vibe home.

On Windows, a folder that Python created with ``mkdir(mode=0o700)`` from an
elevated process belongs to Administrators, and the same user without
elevation is then denied everything in it. Every request that touches the
folder fails the same way, so clients need one error they can explain, with
the folder to repair, rather than a generic failure per request.
"""

from __future__ import annotations

import os
from pathlib import Path

from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server.protocol import ProtocolErrorCode
from vibe.utils.platform import is_windows
from vibe.utils.vibe_home import get_vibe_home

VIBE_HOME_ACCESS_DENIED = "vibe_home_access_denied"


def vibe_home_access_denied(exc: BaseException) -> SessionBackendError | None:
    """The error to answer with when ``exc`` comes from a denied Vibe home path."""
    if not is_windows():
        return None
    denied = _denied_path(exc)
    if denied is None:
        return None
    vibe_home = get_vibe_home()
    if not denied.is_relative_to(vibe_home):
        return None
    return SessionBackendError(
        ProtocolErrorCode.FORBIDDEN,
        f"Windows denies this account access to {denied}",
        {
            "harnessCode": VIBE_HOME_ACCESS_DENIED,
            "path": str(denied),
            "vibeHome": str(vibe_home),
        },
        expected=True,
    )


def _denied_path(exc: BaseException) -> Path | None:
    # The session store and lease code wrap the OS error in their own, so the
    # denial is found along the cause chain.
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, PermissionError) and current.filename:
            return Path(os.fsdecode(current.filename))
        current = current.__cause__ or current.__context__
    return None
