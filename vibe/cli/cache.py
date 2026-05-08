from __future__ import annotations

import logging
from pathlib import Path
import tomllib
from typing import Any

import tomli_w

logger = logging.getLogger(__name__)


def read_cache(cache_path: Path) -> dict[str, Any]:
    """Read the cache.toml file, returning an empty dict on any error."""
    try:
        with cache_path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def write_cache(cache_path: Path, section: str, data: dict[str, Any]) -> None:
    """Write the full cache dict to cache.toml, merging with existing data."""
    existing = read_cache(cache_path)
    existing.setdefault(section, {})
    existing[section].update(data)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("wb") as f:
            tomli_w.dump(existing, f)
    except OSError:
        logger.debug("Failed to write cache file %s", cache_path, exc_info=True)
