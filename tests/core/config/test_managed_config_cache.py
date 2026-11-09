from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vibe.core.config import VibeConfigSchema, managed_config_cache
from vibe.core.config.managed_config_cache import (
    MANAGED_CONFIG_CACHE_TTL_SECONDS,
    load_cached_managed_config,
    managed_config_cache_key,
    store_cached_managed_config,
)


@pytest.fixture
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "managed_config_cache.json"
    monkeypatch.setattr(
        managed_config_cache, "MANAGED_CONFIG_CACHE_FILE", SimpleNamespace(path=path)
    )
    return path


@pytest.fixture
def api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")


def _key() -> str | None:
    return managed_config_cache_key(VibeConfigSchema())


def test_store_and_load_roundtrip(cache_file: Path, api_key: None) -> None:
    store_cached_managed_config(_key(), 'theme = "nord"\n')
    assert load_cached_managed_config(_key()) == 'theme = "nord"\n'


def test_without_a_credential_there_is_no_entry(
    cache_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert _key() is None
    store_cached_managed_config(None, 'theme = "nord"\n')
    assert load_cached_managed_config(None) is None
    assert not cache_file.exists()


def test_expired_entry_returns_none(cache_file: Path, api_key: None) -> None:
    import json

    store_cached_managed_config(_key(), 'theme = "nord"\n')
    data = json.loads(cache_file.read_text())
    (entry,) = data.values()
    entry["stored_at_timestamp"] -= MANAGED_CONFIG_CACHE_TTL_SECONDS
    cache_file.write_text(json.dumps(data))
    assert load_cached_managed_config(_key()) is None


def test_corrupt_payload_returns_none(cache_file: Path, api_key: None) -> None:
    cache_file.write_text('{"key": {"stored_at_timestamp": 1, "payload": 42}}')
    # A non-string payload cannot be managed-config TOML; the caller treats it
    # as no cache and the session opens unenforced rather than crashing.
    assert load_cached_managed_config(_key()) is None


def test_an_empty_policy_is_cached_as_empty_not_as_missing(
    cache_file: Path, api_key: None
) -> None:
    # "The org enforces nothing" is an answer: it is cached, unlike no answer.
    store_cached_managed_config(_key(), "")
    assert load_cached_managed_config(_key()) == ""


def test_entries_are_kept_per_credential(
    cache_file: Path, api_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_cached_managed_config(_key(), 'theme = "nord"\n')
    monkeypatch.setenv("MISTRAL_API_KEY", "other-key")
    store_cached_managed_config(_key(), "")

    assert load_cached_managed_config(_key()) == ""
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    assert load_cached_managed_config(_key()) == 'theme = "nord"\n'
