from __future__ import annotations

import asyncio
import json
from pathlib import Path

from vibe.core.config import SessionLoggingConfig
from vibe.core.session.session_logger import SessionLogger


def migrate_sessions_entrypoint(session_config: SessionLoggingConfig) -> int:
    return asyncio.run(migrate_sessions(session_config))


async def migrate_sessions(session_config: SessionLoggingConfig) -> int:
    """Helper for migrating session data from singular JSON files to the format introduced in Vibe 2.0 with per-session folders with split metadata and message files."""
    save_dir = session_config.save_dir
    if not save_dir or not session_config.enabled:
        return 0

    successful_migrations = 0
    session_files = list(Path(save_dir).glob(f"{session_config.session_prefix}_*.json"))
    for session_file in session_files:
        try:
            with open(session_file) as f:
                session_data = f.read()
            session_json = json.loads(session_data)
            metadata = session_json["metadata"]
            messages = session_json["messages"]

            session_dir = Path(save_dir) / session_file.stem
            session_dir.mkdir()

            await SessionLogger.persist_metadata(metadata, session_dir)
            await SessionLogger.persist_messages(messages, session_dir)
            session_file.unlink()
            successful_migrations += 1
        except Exception:
            continue

    return successful_migrations
