from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.update_notifier.adapters.fake_update_cache_repository import (
    FakeUpdateCacheRepository,
)
from vibe.cli.update_notifier import UpdateCache
from vibe.cli.update_notifier.whats_new import (
    load_whats_new_content,
    mark_version_as_seen,
    should_show_whats_new,
)


@pytest.mark.asyncio
async def test_should_show_whats_new_returns_false_when_cache_is_none() -> None:
    repository = FakeUpdateCacheRepository()

    result = await should_show_whats_new("1.0.0", repository)

    assert result is False


@pytest.mark.asyncio
async def test_should_show_whats_new_returns_true_when_seen_whats_new_version_differs() -> (
    None
):
    cache = UpdateCache(
        latest_version="1.0.0",
        stored_at_timestamp=1_700_000_000,
        seen_whats_new_version="0.9.0",
    )
    repository = FakeUpdateCacheRepository(update_cache=cache)

    result = await should_show_whats_new("1.0.0", repository)

    assert result is True


@pytest.mark.asyncio
async def test_should_show_whats_new_returns_false_when_seen_whats_new_version_matches() -> (
    None
):
    cache = UpdateCache(
        latest_version="1.0.0",
        stored_at_timestamp=1_700_000_000,
        seen_whats_new_version="1.0.0",
    )
    repository = FakeUpdateCacheRepository(update_cache=cache)

    result = await should_show_whats_new("1.0.0", repository)

    assert result is False


@pytest.mark.asyncio
async def test_should_show_whats_new_returns_true_when_seen_whats_new_version_is_none() -> (
    None
):
    cache = UpdateCache(
        latest_version="1.0.0",
        stored_at_timestamp=1_700_000_000,
        seen_whats_new_version=None,
    )
    repository = FakeUpdateCacheRepository(update_cache=cache)

    result = await should_show_whats_new("1.0.0", repository)

    assert result is True


def test_load_whats_new_content_returns_none_when_file_does_not_exist(
    tmp_path: Path,
) -> None:
    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        result = load_whats_new_content()

    assert result is None


def test_load_whats_new_content_returns_none_when_file_is_empty(tmp_path: Path) -> None:
    whats_new_file = tmp_path / "whats_new.md"
    whats_new_file.write_text("")

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        result = load_whats_new_content()

    assert result is None


def test_load_whats_new_content_returns_none_when_file_contains_only_whitespace(
    tmp_path: Path,
) -> None:
    whats_new_file = tmp_path / "whats_new.md"
    whats_new_file.write_text("   \n\t  \n  ")

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        result = load_whats_new_content()

    assert result is None


def test_load_whats_new_content_returns_content_when_file_exists(
    tmp_path: Path,
) -> None:
    whats_new_file = tmp_path / "whats_new.md"
    content = "# What's New\n\n- Feature 1\n- Feature 2"
    whats_new_file.write_text(content)

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        result = load_whats_new_content()

    assert result == content


def test_load_whats_new_content_handles_os_error(tmp_path: Path) -> None:
    whats_new_file = tmp_path / "whats_new.md"
    whats_new_file.write_text("content")

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        with patch.object(Path, "read_text", side_effect=OSError("Permission denied")):
            result = load_whats_new_content()

    assert result is None


@pytest.mark.asyncio
async def test_mark_version_as_seen_creates_new_cache_when_repository_is_empty() -> (
    None
):
    repository = FakeUpdateCacheRepository()

    await mark_version_as_seen("1.0.0", repository)

    assert repository.update_cache is not None
    assert repository.update_cache.latest_version == "1.0.0"
    assert repository.update_cache.seen_whats_new_version == "1.0.0"
    assert repository.update_cache.stored_at_timestamp > 0


@pytest.mark.asyncio
async def test_mark_version_as_seen_updates_seen_whats_new_version_preserving_other_fields() -> (
    None
):
    cache = UpdateCache(
        latest_version="1.2.0",
        stored_at_timestamp=1_700_000_000,
        seen_whats_new_version="1.0.0",
    )
    repository = FakeUpdateCacheRepository(update_cache=cache)

    await mark_version_as_seen("1.1.0", repository)

    assert repository.update_cache is not None
    assert repository.update_cache.latest_version == "1.2.0"
    assert repository.update_cache.stored_at_timestamp == 1_700_000_000
    assert repository.update_cache.seen_whats_new_version == "1.1.0"
