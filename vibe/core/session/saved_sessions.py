from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
from typing import Any

from vibe.core.config import SessionLoggingConfig
from vibe.core.session import last_session_pointer
from vibe.core.session.session_loader import METADATA_FILENAME, SessionLoader
from vibe.core.session.session_logger import SessionLogger
from vibe.utils.io import read_safe


def _normalize_session_title(title: str) -> str:
    normalized_title = title.strip()
    if not normalized_title:
        raise ValueError("Session title cannot be empty.")

    return normalized_title


def _resolve_saved_session_dir(
    session_id: str, session_config: SessionLoggingConfig
) -> Path:
    session_dir = _find_saved_session_dir(session_id, session_config)
    if session_dir is None:
        raise ValueError(f"Session not found: {session_id}")

    return session_dir


def _find_saved_session_dir(
    session_id: str, session_config: SessionLoggingConfig
) -> Path | None:
    for session_dir in SessionLoader._find_session_dirs_by_short_id(
        session_id, session_config
    ):
        try:
            metadata = _load_raw_metadata(session_dir)
        except (OSError, ValueError, json.JSONDecodeError):
            continue

        if metadata.get("session_id") == session_id:
            return session_dir

    return None


def _load_raw_metadata(session_dir: Path) -> dict[str, Any]:
    metadata_path = session_dir / METADATA_FILENAME
    metadata = json.loads(read_safe(metadata_path).text)
    if not isinstance(metadata, dict):
        raise ValueError(f"Session metadata must be an object: {metadata_path}")

    return metadata


async def update_saved_session_title_at_path(
    session_dir: Path, title: str
) -> dict[str, Any]:
    normalized_title = _normalize_session_title(title)
    metadata = _load_raw_metadata(session_dir)

    updated_metadata = {**metadata, "title": normalized_title, "title_source": "manual"}
    await SessionLogger.persist_metadata(updated_metadata, session_dir)
    return updated_metadata


async def update_saved_session_title(
    session_id: str, title: str, session_config: SessionLoggingConfig
) -> dict[str, Any]:
    session_dir = _resolve_saved_session_dir(session_id, session_config)
    return await update_saved_session_title_at_path(session_dir, title)


async def relocate_saved_session_at_path(
    session_dir: Path, cwd: Path
) -> dict[str, Any]:
    """Move a session no agent is holding, by rewriting where it sits.

    The same two edits :meth:`SessionLogger.relocated_to` makes to a live
    session, applied to the record instead: ``environment.working_directory``
    names where the session is working, ``origin_directory`` keeps the place it
    began so the listing can still find it there. A session that predates the
    field has only the environment entry, and that entry is its origin, so the
    first move has to promote it before overwriting.

    Nothing else moves. A detached session holds no trust grant, no harness
    root and no config layer to re-root -- those are built from this record the
    next time it is opened, which is why the move is only this.
    """
    metadata = _load_raw_metadata(session_dir)
    environment = {**(metadata.get("environment") or {})}
    origin = metadata.get("origin_directory") or environment.get("working_directory")
    environment["working_directory"] = str(cwd)

    updated_metadata = {
        **metadata,
        "environment": environment,
        "origin_directory": origin,
    }
    await SessionLogger.persist_metadata(updated_metadata, session_dir)
    return updated_metadata


async def relocate_saved_session(
    session_id: str, cwd: Path, session_config: SessionLoggingConfig
) -> dict[str, Any]:
    session_dir = _resolve_saved_session_dir(session_id, session_config)
    return await relocate_saved_session_at_path(session_dir, cwd)


async def delete_saved_session(
    session_id: str, session_config: SessionLoggingConfig
) -> None:
    session_dir = _find_saved_session_dir(session_id, session_config)
    if session_dir is None:
        last_session_pointer.clear_matching(session_config, session_id)
        return

    await asyncio.to_thread(shutil.rmtree, session_dir)
    last_session_pointer.clear_matching(session_config, session_id)
