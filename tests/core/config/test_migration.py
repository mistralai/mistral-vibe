from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest
import tomli_w

from vibe.core.config._migration import (
    MODEL_RENAME_MIGRATION,
    migrate_config,
    migrate_config_layers,
)
from vibe.core.config.layers.user import UserConfigLayer

type ConfigData = dict[str, Any]
type ModelRepresentation = Literal["list", "map"]

_LEGACY_ALIAS = "devstral-2"
_CURRENT_ALIAS = "mistral-medium-3.5"
_OFFICIAL_NAME = "mistral-vibe-cli-latest"

_CUSTOM_MODEL = {
    "name": "custom-model",
    "provider": "custom-provider",
    "alias": _LEGACY_ALIAS,
    "temperature": 0.4,
    "input_price": 0.1,
    "output_price": 0.2,
    "thinking": "medium",
    "supports_images": False,
    "custom_field": "preserved",
}
_OFFICIAL_LEGACY_MODEL = {
    "name": _OFFICIAL_NAME,
    "provider": "mistral",
    "alias": _LEGACY_ALIAS,
}
_OFFICIAL_CURRENT_MODEL = {
    "name": _OFFICIAL_NAME,
    "provider": "mistral",
    "alias": _CURRENT_ALIAS,
}
_TARGET_MODEL = {
    "name": "custom-target",
    "provider": "custom-provider",
    "alias": _CURRENT_ALIAS,
    "thinking": "off",
}
_UNRELATED_MODEL = {
    "name": "other-model",
    "provider": "mistral",
    "alias": "devstral-2-clone",
    "temperature": 0.5,
    "thinking": "low",
}


def _handled(data: ConfigData) -> ConfigData:
    result = deepcopy(data)
    result["applied_migrations"] = [MODEL_RENAME_MIGRATION]
    return result


def _with_model_representation(
    data: ConfigData, representation: ModelRepresentation
) -> ConfigData:
    result = deepcopy(data)
    models = result.get("models")
    if representation == "map" and isinstance(models, list):
        result["models"] = {model["alias"]: model for model in models}
    return result


_MIGRATION_CASES = [
    pytest.param(
        {"active_model": _LEGACY_ALIAS, "models": [_CUSTOM_MODEL]},
        _handled({"active_model": _LEGACY_ALIAS, "models": [_CUSTOM_MODEL]}),
        id="active-custom-binding",
    ),
    pytest.param(
        {"active_model": "other-model", "models": [_CUSTOM_MODEL]},
        _handled({"active_model": "other-model", "models": [_CUSTOM_MODEL]}),
        id="inactive-custom-binding",
    ),
    pytest.param(
        {
            "active_model": _LEGACY_ALIAS,
            "models": [_OFFICIAL_LEGACY_MODEL, _UNRELATED_MODEL],
        },
        _handled({
            "active_model": _CURRENT_ALIAS,
            "models": [
                {
                    **_OFFICIAL_CURRENT_MODEL,
                    "temperature": 1.0,
                    "input_price": 1.5,
                    "output_price": 7.5,
                    "thinking": "high",
                    "supports_images": True,
                },
                _UNRELATED_MODEL,
            ],
        }),
        id="official-binding",
    ),
    pytest.param(
        {"active_model": _LEGACY_ALIAS},
        _handled({"active_model": _CURRENT_ALIAS}),
        id="historical-default-reference",
    ),
    pytest.param(
        {
            "active_model": _LEGACY_ALIAS,
            "models": [_OFFICIAL_LEGACY_MODEL, _TARGET_MODEL],
        },
        _handled({
            "active_model": _LEGACY_ALIAS,
            "models": [_OFFICIAL_LEGACY_MODEL, _TARGET_MODEL],
        }),
        id="target-collision",
    ),
    pytest.param(
        {"active_model": _CURRENT_ALIAS, "models": [_CUSTOM_MODEL]},
        _handled({"active_model": _CURRENT_ALIAS, "models": [_CUSTOM_MODEL]}),
        id="ambiguous-corrupted-binding",
    ),
    pytest.param(
        {
            "models": [_OFFICIAL_CURRENT_MODEL],
            "applied_migrations": [MODEL_RENAME_MIGRATION],
        },
        {
            "models": [{**_OFFICIAL_CURRENT_MODEL, "supports_images": True}],
            "applied_migrations": [MODEL_RENAME_MIGRATION],
        },
        id="independent-image-backfill",
    ),
]


@pytest.mark.parametrize("initial, expected", _MIGRATION_CASES)
@pytest.mark.parametrize("representation", ["list", "map"])
def test_model_rename_state_matrix(
    initial: ConfigData, expected: ConfigData, representation: ModelRepresentation
) -> None:
    data = _with_model_representation(initial, representation)

    assert migrate_config(data) is True
    assert data == _with_model_representation(expected, representation)

    migrated = deepcopy(data)
    assert migrate_config(data) is False
    assert data == migrated


@pytest.mark.parametrize("representation", ["list", "map"])
def test_marker_preserves_selected_custom_binding(
    representation: ModelRepresentation,
) -> None:
    data = _with_model_representation(
        {
            "active_model": _LEGACY_ALIAS,
            "models": [_CUSTOM_MODEL],
            "applied_migrations": [MODEL_RENAME_MIGRATION],
        },
        representation,
    )
    before = deepcopy(data)

    assert migrate_config(data) is False
    assert data == before


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"theme": "dark"}, id="no-models"),
        pytest.param(
            {
                "models": [
                    {
                        "name": _OFFICIAL_NAME,
                        "provider": "mistral",
                        "alias": "my-custom-alias",
                        "temperature": 0.2,
                        "thinking": "off",
                    },
                    _UNRELATED_MODEL,
                ]
            },
            id="unrelated-models",
        ),
    ],
)
def test_irrelevant_configs_are_unchanged(data: ConfigData) -> None:
    before = deepcopy(data)

    assert migrate_config(data) is False
    assert data == before


def test_current_official_model_backfills_only_missing_image_support() -> None:
    model = {
        **_OFFICIAL_CURRENT_MODEL,
        "temperature": 1.0,
        "input_price": 1.5,
        "output_price": 7.5,
        "thinking": "high",
    }
    data: ConfigData = {"active_model": _CURRENT_ALIAS, "models": [model]}

    assert migrate_config(data) is True
    assert model == {
        **_OFFICIAL_CURRENT_MODEL,
        "temperature": 1.0,
        "input_price": 1.5,
        "output_price": 7.5,
        "thinking": "high",
        "supports_images": True,
    }
    assert migrate_config(data) is False


def test_current_official_model_preserves_explicit_image_support_false() -> None:
    data: ConfigData = {
        "models": [{**_OFFICIAL_CURRENT_MODEL, "supports_images": False}]
    }
    before = deepcopy(data)

    assert migrate_config(data) is False
    assert data == before


@pytest.mark.asyncio
@pytest.mark.parametrize("initial, _expected", _MIGRATION_CASES)
async def test_second_pass_preserves_file_and_fingerprint_for_every_state(
    tmp_working_directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    initial: ConfigData,
    _expected: ConfigData,
) -> None:
    path = tmp_working_directory / "config.toml"
    with path.open("wb") as file:
        tomli_w.dump(deepcopy(initial), file)
    layer = UserConfigLayer(path=path)

    await migrate_config_layers([layer])
    before = path.read_bytes()
    fingerprint = layer.fingerprint
    apply_spy = AsyncMock(wraps=layer.apply)
    monkeypatch.setattr(layer, "apply", apply_spy)

    await migrate_config_layers([layer])

    apply_spy.assert_not_awaited()
    assert path.read_bytes() == before
    assert layer.fingerprint == fingerprint
