from __future__ import annotations

import asyncio
import json
from pathlib import Path

from vibe.cli.cache import read_cache, write_cache
from vibe.cli.update_notifier.ports.update_cache_repository import (
    UpdateCache,
    UpdateCacheRepository,
)
from vibe.core.paths import VIBE_HOME

_CACHE_SECTION = "update_cache"


class FileSystemUpdateCacheRepository(UpdateCacheRepository):
    def __init__(self, base_path: Path | str | None = None) -> None:
        self._base_path = Path(base_path) if base_path is not None else VIBE_HOME.path
        self._cache_file = self._base_path / "cache.toml"
        self._legacy_json = self._base_path / "update_cache.json"

    async def get(self) -> UpdateCache | None:
        data = await asyncio.to_thread(self._read_section)
        if data is None:
            return None
        return self._parse(data)

    async def set(self, update_cache: UpdateCache) -> None:
        payload: dict[str, str | int] = {
            "latest_version": update_cache.latest_version,
            "stored_at_timestamp": update_cache.stored_at_timestamp,
        }
        if update_cache.seen_whats_new_version is not None:
            payload["seen_whats_new_version"] = update_cache.seen_whats_new_version
        await asyncio.to_thread(write_cache, self._cache_file, _CACHE_SECTION, payload)

    def _read_section(self) -> dict | None:
        cache = read_cache(self._cache_file)
        if section := cache.get(_CACHE_SECTION):
            return section

        try:
            data = json.loads(self._legacy_json.read_text())
        except (OSError, json.JSONDecodeError):
            return None

        if isinstance(data, dict):
            write_cache(
                self._cache_file,
                _CACHE_SECTION,
                {k: v for k, v in data.items() if v is not None},
            )
        return data

    @staticmethod
    def _parse(data: dict) -> UpdateCache | None:
        latest_version = data.get("latest_version")
        stored_at_timestamp = data.get("stored_at_timestamp")
        seen_whats_new_version = data.get("seen_whats_new_version")

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
