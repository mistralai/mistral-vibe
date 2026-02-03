from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe.cli.update_notifier.adapters.filesystem_update_cache_repository import (
    FileSystemUpdateCacheRepository,
)
from vibe.cli.update_notifier.ports.update_cache_repository import UpdateCache


@pytest.mark.asyncio
async def test_reads_cache_from_file_when_present(tmp_path: Path) -> None:
    cache_file = tmp_path / "update_cache.json"
    cache_file.write_text(
        json.dumps({"latest_version": "1.2.3", "stored_at_timestamp": 1_700_000_000})
    )
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    cache = await repository.get()

    assert cache is not None
    assert cache.latest_version == "1.2.3"
    assert cache.stored_at_timestamp == 1_700_000_000
    assert cache.seen_whats_new_version is None


@pytest.mark.asyncio
async def test_returns_none_when_cache_file_is_missing(tmp_path: Path) -> None:
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    cache = await repository.get()

    assert cache is None


@pytest.mark.asyncio
async def test_returns_none_when_cache_file_is_corrupted(tmp_path: Path) -> None:
    cache_dir = tmp_path / ".vibe"
    cache_dir.mkdir()
    (cache_dir / "update_cache.json").write_text("{not-json")
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    cache = await repository.get()

    assert cache is None


@pytest.mark.asyncio
async def test_overwrites_existing_cache(tmp_path: Path) -> None:
    cache_file = tmp_path / "update_cache.json"
    cache_file.write_text(
        json.dumps({"latest_version": "1.0.0", "stored_at_timestamp": 1_600_000_000})
    )
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    await repository.set(
        UpdateCache(latest_version="1.1.0", stored_at_timestamp=1_700_200_000)
    )

    content = json.loads(cache_file.read_text())
    assert content["latest_version"] == "1.1.0"
    assert content["stored_at_timestamp"] == 1_700_200_000
    assert content.get("seen_whats_new_version") is None


@pytest.mark.asyncio
async def test_reads_cache_with_seen_whats_new_version(tmp_path: Path) -> None:
    cache_file = tmp_path / "update_cache.json"
    cache_file.write_text(
        json.dumps({
            "latest_version": "1.2.3",
            "stored_at_timestamp": 1_700_000_000,
            "seen_whats_new_version": "1.2.0",
        })
    )
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    cache = await repository.get()

    assert cache is not None
    assert cache.latest_version == "1.2.3"
    assert cache.stored_at_timestamp == 1_700_000_000
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

    cache_file = tmp_path / "update_cache.json"
    content = json.loads(cache_file.read_text())
    assert content["latest_version"] == "1.1.0"
    assert content["stored_at_timestamp"] == 1_700_200_000
    assert content["seen_whats_new_version"] == "1.1.0"


@pytest.mark.asyncio
async def test_silently_ignores_errors_when_writing_cache_fails(tmp_path: Path) -> None:
    cache_dir = tmp_path / ".vibe"
    cache_dir.mkdir()
    (cache_dir / "update_cache.json").mkdir()
    repository = FileSystemUpdateCacheRepository(base_path=tmp_path)

    await repository.set(
        UpdateCache(latest_version="1.2.0", stored_at_timestamp=1_700_300_000)
    )

    assert (cache_dir / "update_cache.json").is_dir()
