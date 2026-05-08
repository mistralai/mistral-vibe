from __future__ import annotations

import json
from pathlib import Path
import tomllib

import pytest
import tomli_w

from vibe.cli.update_notifier.adapters.filesystem_update_cache_repository import (
    FileSystemUpdateCacheRepository,
)
from vibe.cli.update_notifier.ports.update_cache_repository import UpdateCache


def _write_cache_toml(base: Path, data: dict) -> None:
    with (base / "cache.toml").open("wb") as f:
        tomli_w.dump(data, f)


@pytest.mark.asyncio
async def test_reads_cache_from_toml_when_present(tmp_path: Path) -> None:
    _write_cache_toml(
        tmp_path,
        {
            "update_cache": {
                "latest_version": "1.2.3",
                "stored_at_timestamp": 1_700_000_000,
            }
        },
    )
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    cache = await repository.get()

    assert cache is not None
    assert cache.latest_version == "1.2.3"
    assert cache.stored_at_timestamp == 1_700_000_000
    assert cache.seen_whats_new_version is None


@pytest.mark.asyncio
async def test_falls_back_to_json_when_toml_missing(tmp_path: Path) -> None:
    (tmp_path / "update_cache.json").write_text(
        json.dumps({"latest_version": "1.0.0", "stored_at_timestamp": 1_600_000_000})
    )
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    cache = await repository.get()

    assert cache is not None
    assert cache.latest_version == "1.0.0"
    assert cache.stored_at_timestamp == 1_600_000_000


@pytest.mark.asyncio
async def test_returns_none_when_no_cache_exists(tmp_path: Path) -> None:
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    cache = await repository.get()

    assert cache is None


@pytest.mark.asyncio
async def test_returns_none_when_toml_is_corrupted(tmp_path: Path) -> None:
    (tmp_path / "cache.toml").write_text("{not-toml")
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    cache = await repository.get()

    assert cache is None


@pytest.mark.asyncio
async def test_set_writes_to_toml(tmp_path: Path) -> None:
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    await repository.set(
        UpdateCache(latest_version="1.1.0", stored_at_timestamp=1_700_200_000)
    )

    with (tmp_path / "cache.toml").open("rb") as f:
        data = tomllib.load(f)
    assert data["update_cache"]["latest_version"] == "1.1.0"
    assert data["update_cache"]["stored_at_timestamp"] == 1_700_200_000
    assert data["update_cache"].get("seen_whats_new_version") is None


@pytest.mark.asyncio
async def test_reads_cache_with_seen_whats_new_version(tmp_path: Path) -> None:
    _write_cache_toml(
        tmp_path,
        {
            "update_cache": {
                "latest_version": "1.2.3",
                "stored_at_timestamp": 1_700_000_000,
                "seen_whats_new_version": "1.2.0",
            }
        },
    )
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    cache = await repository.get()

    assert cache is not None
    assert cache.seen_whats_new_version == "1.2.0"


@pytest.mark.asyncio
async def test_writes_cache_with_seen_whats_new_version(tmp_path: Path) -> None:
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    await repository.set(
        UpdateCache(
            latest_version="1.1.0",
            stored_at_timestamp=1_700_200_000,
            seen_whats_new_version="1.1.0",
        )
    )

    with (tmp_path / "cache.toml").open("rb") as f:
        data = tomllib.load(f)
    assert data["update_cache"]["seen_whats_new_version"] == "1.1.0"


@pytest.mark.asyncio
async def test_set_preserves_other_sections(tmp_path: Path) -> None:
    _write_cache_toml(tmp_path, {"feedback": {"last_shown_at": 123.0}})
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    await repository.set(
        UpdateCache(latest_version="2.0.0", stored_at_timestamp=1_800_000_000)
    )

    with (tmp_path / "cache.toml").open("rb") as f:
        data = tomllib.load(f)
    assert data["feedback"]["last_shown_at"] == 123.0
    assert data["update_cache"]["latest_version"] == "2.0.0"


@pytest.mark.asyncio
async def test_prefers_toml_over_json(tmp_path: Path) -> None:
    _write_cache_toml(
        tmp_path,
        {
            "update_cache": {
                "latest_version": "2.0.0",
                "stored_at_timestamp": 1_800_000_000,
            }
        },
    )
    (tmp_path / "update_cache.json").write_text(
        json.dumps({"latest_version": "1.0.0", "stored_at_timestamp": 1_600_000_000})
    )
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    cache = await repository.get()

    assert cache is not None
    assert cache.latest_version == "2.0.0"
