from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from vibe.core.model_catalog import (
    ModelCatalogResponse,
    cache,
    load_cached_model_catalog,
    parse_model_catalog_response,
    store_cached_model_catalog,
)
from vibe.core.paths import MODEL_CATALOG_CACHE_FILE


def _catalog(models: list[dict[str, Any]] | None = None) -> ModelCatalogResponse:
    payload = {
        "models": models
        if models is not None
        else [
            {
                "id": "mistral-medium-3.5",
                "label": "Mistral Medium 3.5",
                "recommended": True,
            }
        ]
    }
    parsed = parse_model_catalog_response(payload)
    assert parsed is not None
    return parsed


def _config(*, has_credential: bool = True) -> Any:
    config = MagicMock()
    if has_credential:
        provider = MagicMock(api_base="https://x")
        # Unset env var: the real credential resolver (used by the
        # no-credential test path) yields no key, and the fixed-key fixture
        # bypasses resolution everywhere else.
        provider.api_key_env_var = "VIBE_TEST_UNSET_CATALOG_KEY"
        config.get_mistral_provider.return_value = provider
    else:
        config.get_mistral_provider.return_value = None
    return config


@pytest.fixture
def _fixed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "_cache_key", lambda _config: "user-abc")


def test_store_then_load_round_trips(_fixed_key: None) -> None:
    store_cached_model_catalog(_config(), _catalog())

    loaded = load_cached_model_catalog(_config())

    assert loaded is not None
    assert loaded.models[0].id == "mistral-medium-3.5"


def test_load_returns_none_when_cache_missing(_fixed_key: None) -> None:
    assert load_cached_model_catalog(_config()) is None


def test_load_returns_none_when_entry_is_stale(_fixed_key: None) -> None:
    store_cached_model_catalog(_config(), _catalog())

    path = MODEL_CATALOG_CACHE_FILE.path
    entries = json.loads(path.read_text())
    entries["user-abc"]["stored_at_timestamp"] -= (
        cache.MODEL_CATALOG_CACHE_TTL_SECONDS + 1
    )
    path.write_text(json.dumps(entries))

    assert load_cached_model_catalog(_config()) is None


def test_load_fails_open_on_corrupt_file(_fixed_key: None) -> None:
    path = MODEL_CATALOG_CACHE_FILE.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid json")

    assert load_cached_model_catalog(_config()) is None


def test_store_overwrites_previous_catalog_with_empty_list(_fixed_key: None) -> None:
    store_cached_model_catalog(_config(), _catalog())
    store_cached_model_catalog(_config(), _catalog(models=[]))

    loaded = load_cached_model_catalog(_config())

    assert loaded is not None
    assert loaded.models == []


def test_entries_are_isolated_per_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    current = {"key": "user-a"}
    monkeypatch.setattr(cache, "_cache_key", lambda _config: current["key"])

    store_cached_model_catalog(_config(), _catalog())
    current["key"] = "user-b"
    store_cached_model_catalog(_config(), _catalog(models=[]))

    loaded_b = load_cached_model_catalog(_config())
    current["key"] = "user-a"
    loaded_a = load_cached_model_catalog(_config())

    assert loaded_b is not None
    assert loaded_b.models == []
    assert loaded_a is not None
    assert loaded_a.models[0].id == "mistral-medium-3.5"


def test_no_credential_means_no_cache_access() -> None:
    config = _config(has_credential=False)

    assert cache._cache_key(config) is None
    store_cached_model_catalog(config, _catalog())
    assert load_cached_model_catalog(config) is None
