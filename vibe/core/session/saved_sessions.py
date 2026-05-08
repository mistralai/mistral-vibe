from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vibe.core.config import SessionLoggingConfig
from vibe.core.session.session_loader import METADATA_FILENAME, SessionLoader
from vibe.core.session.session_logger import SessionLogger
from vibe.core.utils.io import read_safe


def _normalize_session_title(title: str) -> str:
    normalized_title = title.strip()
    if not normalized_title:
        raise ValueError("Session title cannot be empty.")

    return normalized_title


def _resolve_saved_session_dir(
    session_id: str, session_config: SessionLoggingConfig
) -> Path:
    for session_dir in SessionLoader._find_session_dirs_by_short_id(
        session_id, session_config
    ):
        try:
            metadata = _load_raw_metadata(session_dir)
        except (OSError, json.JSONDecodeError):
            continue

        if metadata.get("session_id") == session_id:
            return session_dir

    raise ValueError(f"Session not found: {session_id}")


def _load_raw_metadata(session_dir: Path) -> dict[str, Any]:
    metadata_path = session_dir / METADATA_FILENAME
    return json.loads(read_safe(metadata_path).text)


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
