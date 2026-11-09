from __future__ import annotations

from vibe.core.model_catalog import parse_model_catalog_response

_VALID_ENTRY = {
    "id": "mistral-medium-3.5",
    "label": "Mistral Medium 3.5",
    "aliases": ["mistral-medium-2505"],
    "recommended": True,
    "max_context_length": 262144,
    "parameters": {"temperature": 0.3, "reasoning": ["off", "high"]},
    "capabilities": {"function_calling": True, "vision": True},
}


def test_parse_reads_all_fields() -> None:
    catalog = parse_model_catalog_response({"models": [_VALID_ENTRY]})

    assert catalog is not None
    assert len(catalog.models) == 1
    entry = catalog.models[0]
    assert entry.id == "mistral-medium-3.5"
    assert entry.label == "Mistral Medium 3.5"
    assert entry.aliases == ["mistral-medium-2505"]
    assert entry.recommended is True
    assert entry.max_context_length == 262144
    assert entry.parameters.temperature == 0.3
    assert entry.parameters.reasoning == ["off", "high"]
    assert entry.capabilities.function_calling is True
    assert entry.capabilities.vision is True


def test_parse_ignores_unknown_fields() -> None:
    payload = {"models": [{**_VALID_ENTRY, "pricing": {"input": 1}}], "extra": 1}

    catalog = parse_model_catalog_response(payload)

    assert catalog is not None
    assert len(catalog.models) == 1


def test_parse_defaults_missing_optional_fields() -> None:
    catalog = parse_model_catalog_response({"models": [{"id": "a", "label": "A"}]})

    assert catalog is not None
    entry = catalog.models[0]
    assert entry.aliases == []
    assert entry.recommended is False
    assert entry.max_context_length is None
    assert entry.parameters.temperature is None
    assert entry.capabilities.function_calling is False


def test_parse_skips_one_malformed_entry() -> None:
    payload = {
        "models": [{"label": "no id"}, _VALID_ENTRY, {"id": "", "label": "empty"}]
    }

    catalog = parse_model_catalog_response(payload)

    assert catalog is not None
    assert [entry.id for entry in catalog.models] == ["mistral-medium-3.5"]


def test_parse_all_malformed_entries_is_empty_catalog() -> None:
    catalog = parse_model_catalog_response({"models": [{"label": "no id"}]})

    assert catalog is not None
    assert catalog.models == []


def test_parse_empty_models_is_an_empty_catalog_not_a_failure() -> None:
    catalog = parse_model_catalog_response({"models": []})

    assert catalog is not None
    assert catalog.models == []


def test_parse_returns_none_for_non_catalog_payloads() -> None:
    assert parse_model_catalog_response(None) is None
    assert parse_model_catalog_response(["models"]) is None
    assert parse_model_catalog_response({}) is None
    assert parse_model_catalog_response({"models": {"not": "a list"}}) is None
