from __future__ import annotations

import asyncio
import json
from pathlib import Path

from vibe.cli.update_notifier.ports.update_cache_repository import (
    UpdateCache,
    UpdateCacheRepository,
)
from vibe.core.paths.global_paths import VIBE_HOME


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
            seen_whats_new_version = data.get("seen_whats_new_version")
        except (TypeError, json.JSONDecodeError):
            return None

        if not isinstance(latest_version, str) or not isinstance(
            stored_at_timestamp, int
        ):
            return None

        if (
            not isinstance(seen_whats_new_version, str)
            and seen_whats_new_version is not None
        ):
            seen_whats_new_version = None

        return UpdateCache(
            latest_version=latest_version,
            stored_at_timestamp=stored_at_timestamp,
            seen_whats_new_version=seen_whats_new_version,
        )

    async def set(self, update_cache: UpdateCache) -> None:
        try:
            payload = json.dumps(
                {
                    "latest_version": update_cache.latest_version,
                    "stored_at_timestamp": update_cache.stored_at_timestamp,
                    "seen_whats_new_version": update_cache.seen_whats_new_version,
                },
                ensure_ascii=False,
            )
            await asyncio.to_thread(self._cache_file.write_text, payload)
        except OSError:
            return None
