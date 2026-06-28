from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any, Protocol

import tomli_w

from vibe.core.logger import logger
from vibe.core.paths import CACHE_FILE

__all__ = [
    "FileSystemVibeCodeCacheStore",
    "InMemoryVibeCodeCacheStore",
    "VibeCodeCacheStore",
]


class VibeCodeCacheStore(Protocol):
    def read_section(self, section: str) -> dict[str, Any]: ...

    def write_section(self, section: str, data: dict[str, Any]) -> None: ...


class InMemoryVibeCodeCacheStore:
    def __init__(self) -> None:
        self._sections: dict[str, dict[str, Any]] = {}

    def read_section(self, section: str) -> dict[str, Any]:
        return dict(self._sections.get(section, {}))

    def write_section(self, section: str, data: dict[str, Any]) -> None:
        self._sections.setdefault(section, {}).update(data)


class FileSystemVibeCodeCacheStore:
    def __init__(self, cache_path: Path | str | None = None) -> None:
        self._cache_path = (
            Path(cache_path) if cache_path is not None else CACHE_FILE.path
        )

    def read_section(self, section: str) -> dict[str, Any]:
        data = self._read_cache().get(section)
        if not isinstance(data, dict):
            return {}
        return dict(data)

    def write_section(self, section: str, data: dict[str, Any]) -> None:
        existing = self._read_cache()
        section_data = existing.get(section)
        if not isinstance(section_data, dict):
            section_data = {}
            existing[section] = section_data
        section_data.update(data)
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self._cache_path.open("wb") as f:
                tomli_w.dump(existing, f)
        except OSError:
            logger.debug(
                "Failed to write cache file %s", self._cache_path, exc_info=True
            )

    def _read_cache(self) -> dict[str, Any]:
        try:
            with self._cache_path.open("rb") as f:
                return tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            return {}
