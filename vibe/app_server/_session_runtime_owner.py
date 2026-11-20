"""Refusals for target runtime sessions, which this app server cannot run yet.

A stored session opens on the runtime in its ``meta.json``, whatever
``experimental_target_runtime`` says now, never on the other runtime.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server.protocol import ProtocolErrorCode
from vibe.core.config import VibeConfigSchema
from vibe.core.session.session_loader import METADATA_FILENAME
from vibe.utils.io import read_safe

TARGET_RUNTIME_UNSUPPORTED = "target_runtime_unsupported"


def refuse_target_start(config: VibeConfigSchema) -> None:
    """Refuse to use a new session while the opt-in is on."""
    if not config.experimental_target_runtime:
        return
    raise _unsupported(
        "experimental_target_runtime (VIBE_EXPERIMENTAL_TARGET_RUNTIME) is on, but "
        "this version of Vibe cannot run Sessions on the target runtime yet. "
        "Turn it off to use a new Session."
    )


async def refuse_unless_local(session_dir: Path) -> None:
    """Refuse to open a stored session that the local runtime does not own."""
    owner = await asyncio.to_thread(_stored_owner, session_dir)
    if owner == "local":
        return
    raise _unsupported(
        f"Session {session_dir.name} runs on the {owner} runtime, which this "
        "version of Vibe cannot open."
    )


def _stored_owner(session_dir: Path) -> str:
    """The recorded owner, read from the raw file so that a field this build
    cannot validate does not make a target session look local.
    """
    try:
        raw = json.loads(read_safe(session_dir / METADATA_FILENAME).text)
    except (OSError, json.JSONDecodeError):
        return "local"
    owner = raw.get("runtime") if isinstance(raw, dict) else None
    return "local" if owner is None else str(owner)


def _unsupported(message: str) -> SessionBackendError:
    return SessionBackendError(
        ProtocolErrorCode.NOT_IMPLEMENTED,
        message,
        {"reason": TARGET_RUNTIME_UNSUPPORTED},
        expected=True,
    )
