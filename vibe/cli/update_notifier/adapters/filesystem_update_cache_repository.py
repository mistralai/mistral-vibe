from __future__ import annotations

import asyncio
import json
from pathlib import Path

from vibe.cli.update_notifier.ports.update_cache_repository import (
    UpdateCache,
    UpdateCacheRepository,
)
from vibe.core.config_path import VIBE_HOME


class FileSystemUpdateCacheRepository(UpdateCacheRepository):
    def __init__(self, base_path: Path | str | None = None) -> None:
        self._base_path = Path(base_path) if base_path is not None else VIBE_HOME.path
        self._cache_file = self._base_path / "update_cache.json"

    async def get(self) -> UpdateCache | None:
        try:
            content = await asyncio.to_thread(self._cache_file.read_text)
        except OSError:
            return None

        try:
            data = json.loads(content)
            latest_version = data.get("latest_version")
            stored_at_timestamp = data.get("stored_at_timestamp")
        except (TypeError, json.JSONDecodeError):
            return None

        if not isinstance(latest_version, str) or not isinstance(
            stored_at_timestamp, int
        ):
            return None

        return UpdateCache(
            latest_version=latest_version, stored_at_timestamp=stored_at_timestamp
        )

    async def set(self, update_cache: UpdateCache) -> None:
        try:
            payload = json.dumps({
                "latest_version": update_cache.latest_version,
                "stored_at_timestamp": update_cache.stored_at_timestamp,
            })
            await asyncio.to_thread(self._cache_file.write_text, payload)
        except OSError:
            return None
