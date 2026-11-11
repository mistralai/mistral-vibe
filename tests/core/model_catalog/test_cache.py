from __future__ import annotations

import json
from typing import Any

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


def _loaded(key: str) -> ModelCatalogResponse | None:
    record = load_cached_model_catalog(key)
    return record[0] if record is not None else None


def test_store_then_load_round_trips() -> None:
    store_cached_model_catalog("user-abc", _catalog())

    loaded = _loaded("user-abc")

    assert loaded is not None
    assert loaded.models[0].id == "mistral-medium-3.5"


def test_load_returns_none_when_cache_missing() -> None:
    assert load_cached_model_catalog("user-abc") is None


def test_load_returns_none_when_entry_is_stale() -> None:
    store_cached_model_catalog("user-abc", _catalog())

    path = MODEL_CATALOG_CACHE_FILE.path
    entries = json.loads(path.read_text())
    entries["user-abc"]["stored_at_timestamp"] -= (
        cache.MODEL_CATALOG_CACHE_TTL_SECONDS + 1
    )
    path.write_text(json.dumps(entries))

    assert load_cached_model_catalog("user-abc") is None


def test_load_fails_open_on_corrupt_file() -> None:
    path = MODEL_CATALOG_CACHE_FILE.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid json")

    assert load_cached_model_catalog("user-abc") is None


def test_store_overwrites_previous_catalog_with_empty_list() -> None:
    store_cached_model_catalog("user-abc", _catalog())
    store_cached_model_catalog("user-abc", _catalog(models=[]))

    loaded = _loaded("user-abc")

    assert loaded is not None
    assert loaded.models == []


def test_entries_are_isolated_per_key() -> None:
    store_cached_model_catalog("user-a", _catalog())
    store_cached_model_catalog("user-b", _catalog(models=[]))

    loaded_a = _loaded("user-a")
    loaded_b = _loaded("user-b")

    assert loaded_b is not None
    assert loaded_b.models == []
    assert loaded_a is not None
    assert loaded_a.models[0].id == "mistral-medium-3.5"


def test_the_stored_time_is_kept() -> None:
    store_cached_model_catalog("user-abc", _catalog(), stored_at=1_900_000_000)

    record = load_cached_model_catalog("user-abc")

    assert record is not None
    assert record[1] == 1_900_000_000
