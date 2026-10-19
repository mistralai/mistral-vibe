"""Atomic metadata updates for Unified sessions and historical-session cleanup."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from vibe.core.session.session_loader import METADATA_FILENAME

__all__ = ["persist_metadata"]


def _persist_metadata_sync(metadata: dict[str, Any], session_dir: Path) -> None:
    temp_metadata_filepath = None
    metadata_filepath = session_dir / METADATA_FILENAME
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json.tmp",
            dir=str(session_dir),
            delete=False,
            encoding="utf-8",
        ) as f:
            temp_metadata_filepath = Path(f.name)
            f.write(json.dumps(metadata, indent=2, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_metadata_filepath, str(metadata_filepath))
    except Exception as e:
        raise RuntimeError(
            f"Failed to persist session metadata to {metadata_filepath}: {e}"
        ) from e
    finally:
        if (
            temp_metadata_filepath
            and temp_metadata_filepath.exists()
            and temp_metadata_filepath.is_file()
        ):
            temp_metadata_filepath.unlink()


async def persist_metadata(metadata: dict[str, Any], session_dir: Path) -> None:
    await asyncio.to_thread(_persist_metadata_sync, metadata, session_dir)
