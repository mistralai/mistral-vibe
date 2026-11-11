from __future__ import annotations

import json
import logging
from pathlib import Path
import tomllib
from typing import Any

import pytest
import tomli_w

from vibe.app_server._projection import project_config_view
from vibe.core.config import (
    VibeConfigSchema,
    _catalog_filter,
    build_default_orchestrator,
)
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.model_catalog import apply_model_catalog
from vibe.core.config.vibe_schema import DEFAULT_ACTIVE_MODEL_CONFIG
from vibe.core.experiments.active import ExperimentName
from vibe.core.model_catalog import ModelCatalogResponse, parse_model_catalog_response

_LARGE = "mistral-large-4-0"
_GLM = "zai-glm-5-3"
_FLASH = "zai-glm-5-3-flash"
_DEVSTRAL = "devstral-2"


@pytest.fixture(autouse=True)
def _fresh_id_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_catalog_filter, "_warned_ids", set())


def _entry(model_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": model_id,
        "label": model_id.upper(),
        "capabilities": {"function_calling": True},
        **fields,
    }


def _catalog() -> ModelCatalogResponse:
    """Large 4 (recommended), GLM 5.3, GLM 5.3 Flash (also called ``glm-flash``),
    Devstral 2.
    """
    parsed = parse_model_catalog_response({
        "models": [
            _entry(_LARGE, recommended=True, parameters={"reasoning": ["low", "high"]}),
            _entry(_GLM),
            _entry(_FLASH, aliases=["glm-flash"]),
            _entry(_DEVSTRAL),
        ]
    })
    assert parsed is not None
    return parsed


def _write_user_config(config_dir: Path, **fields: Any) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config.update(fields)
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


async def _config(
    catalog_filter: object | None, *, config_dir: Path | None = None, **user_fields: Any
) -> VibeConfigSchema:
    """The config on the example catalog and *catalog_filter*.

    Unpinned unless *user_fields* pins a model: the test user config pins one.
    """
    orchestrator = await build_default_orchestrator()
    if catalog_filter is not None:
        growthbook = orchestrator.get_layer(GrowthbookLayer.NAME)
        assert isinstance(growthbook, GrowthbookLayer)
        growthbook.set_variants({
            ExperimentName.MODEL_CATALOG.value: True,
            ExperimentName.MODEL_CATALOG_FILTER.value: catalog_filter,
        })
    await apply_model_catalog(orchestrator, _catalog())
    # Written once the catalog is in the stack, like a session sees it.
    if config_dir is not None:
        _write_user_config(config_dir, **{"active_model": "", **user_fields})
        await orchestrator.reload()
    return orchestrator.config


def _catalog_order(config: VibeConfigSchema) -> list[str]:
    return [
        model.alias
        for model in project_config_view(config).models
        if model.source == "catalog"
    ]


# hide


@pytest.mark.asyncio
async def test_a_hidden_model_is_left_out_of_the_model_list() -> None:
    config = await _config({"hide": [_FLASH]})

    assert _FLASH not in config.models
    assert _catalog_order(config) == [_LARGE, _GLM, _DEVSTRAL]


@pytest.mark.asyncio
@pytest.mark.parametrize("pin", [_FLASH, "glm-flash"], ids=["id", "catalog-alias"])
async def test_a_session_pinned_on_a_hidden_model_runs_the_default(
    config_dir: Path, pin: str
) -> None:
    config = await _config({"hide": [_FLASH]}, config_dir=config_dir, active_model=pin)

    assert config.get_active_model().alias == _LARGE
    assert config.validation_warnings == (
        f"Model '{pin}' is not available — running '{_LARGE.upper()}'. "
        "Pick another model to stop this notice.",
    )


@pytest.mark.asyncio
async def test_a_full_user_model_under_a_hidden_id_stays(config_dir: Path) -> None:
    config = await _config(
        {"hide": [_FLASH]},
        config_dir=config_dir,
        models=[{"name": "local-flash", "provider": "llamacpp", "alias": _FLASH}],
    )

    assert config.models[_FLASH].provider == "llamacpp"


@pytest.mark.asyncio
async def test_a_user_setting_on_a_hidden_model_is_ignored(config_dir: Path) -> None:
    config = await _config(
        {"hide": [_FLASH]},
        config_dir=config_dir,
        models=[{"name": _FLASH, "thinking": "low"}],
    )

    assert _FLASH not in config.models
    assert config.validation_warnings == (
        f"Settings for model '{_FLASH}' are ignored: no such model is available.",
    )


@pytest.mark.asyncio
async def test_hiding_every_model_leaves_the_built_in_models(config_dir: Path) -> None:
    config = await _config(
        {"hide": [_LARGE, _GLM, _FLASH, _DEVSTRAL]}, config_dir=config_dir, models=[]
    )

    assert list(config.models) == [DEFAULT_ACTIVE_MODEL_CONFIG.alias]
    assert config.resolve_default_model_alias() == DEFAULT_ACTIVE_MODEL_CONFIG.alias


# default


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("catalog_filter", "default"),
    [
        ({"default": _GLM}, _GLM),
        ({"default": "not-in-the-catalog"}, _LARGE),
        ({"default": _GLM, "hide": [_GLM]}, _LARGE),
        ({"hide": [_LARGE]}, _GLM),
    ],
    ids=["listed", "unlisted-falls-to-recommended", "hidden", "recommended-hidden"],
)
async def test_the_default_model(
    config_dir: Path, catalog_filter: dict[str, Any], default: str
) -> None:
    config = await _config(catalog_filter, config_dir=config_dir)

    assert config.resolve_default_model_alias() == default
    assert config.get_active_model().alias == default


@pytest.mark.asyncio
async def test_a_pin_wins_over_the_filter_default(config_dir: Path) -> None:
    config = await _config(
        {"default": _GLM}, config_dir=config_dir, active_model=_DEVSTRAL
    )

    assert config.get_active_model().alias == _DEVSTRAL
    assert config.resolve_default_model_alias() == _GLM


@pytest.mark.asyncio
async def test_allowed_models_still_excludes_the_filter_default(
    config_dir: Path,
) -> None:
    config = await _config(
        {"default": _GLM}, config_dir=config_dir, allowed_models=[_LARGE, _DEVSTRAL]
    )

    assert config.resolve_default_model_alias() == _LARGE


# order


@pytest.mark.asyncio
async def test_listed_models_come_first_then_the_catalog_order() -> None:
    config = await _config({"order": [_DEVSTRAL, "not-in-the-catalog", _GLM]})

    assert _catalog_order(config) == [_DEVSTRAL, _GLM, _LARGE, _FLASH]


@pytest.mark.asyncio
async def test_other_picker_sections_keep_their_place(config_dir: Path) -> None:
    config = await _config(
        {"order": [_DEVSTRAL]},
        config_dir=config_dir,
        models=[{"name": "local-model", "provider": "llamacpp", "alias": "local"}],
    )

    view = project_config_view(config)
    assert [(model.alias, model.source) for model in view.models][-1] == (
        "local",
        "local",
    )


# settings


@pytest.mark.asyncio
async def test_settings_apply_to_the_catalog_model() -> None:
    config = await _config({
        "settings": {
            _LARGE: {
                "thinking": "low",
                "temperature": 0.3,
                "display_name": "Large 4 (beta)",
                "auto_compact_threshold": 50000,
                "input_price": 1.5,
            }
        }
    })

    model = config.models[_LARGE]
    assert model.thinking == "low"
    assert model.temperature == 0.3
    assert model.display_name == "Large 4 (beta)"
    assert model.auto_compact_threshold == 50000
    assert model.input_price == 1.5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fields", "thinking"),
    [
        ({"thinking_levels": ["off", "low"]}, "low"),
        ({"thinking_levels": ["off", "low"], "thinking": "off"}, "off"),
        ({"thinking_levels": ["low", "high", "max"]}, "high"),
    ],
    ids=["start-level-dropped", "explicit-thinking", "start-level-kept"],
)
async def test_narrowed_levels_move_a_start_level_they_drop(
    fields: dict[str, Any], thinking: str
) -> None:
    config = await _config({"settings": {_LARGE: fields}})

    assert config.models[_LARGE].thinking == thinking


@pytest.mark.asyncio
async def test_the_users_own_setting_wins(config_dir: Path) -> None:
    config = await _config(
        {"settings": {_LARGE: {"thinking": "low"}}},
        config_dir=config_dir,
        models=[{"name": _LARGE, "thinking": "high"}],
    )

    assert config.models[_LARGE].thinking == "high"


@pytest.mark.asyncio
async def test_settings_for_an_unlisted_model_add_no_model() -> None:
    config = await _config({"settings": {"not-in-the-catalog": {"temperature": 0.3}}})

    assert "not-in-the-catalog" not in config.models
    assert config.validation_warnings == ()


@pytest.mark.asyncio
async def test_a_rejected_setting_is_dropped_and_the_others_apply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        config = await _config({
            "settings": {
                _LARGE: {
                    "thinking": "low",
                    "temperature": "hot",
                    "provider": "openai",
                    "top_p": 2,
                    "max_context_length": 1000,
                    "input_price": -5,
                    "cached_input_price": None,
                }
            }
        })

    model = config.models[_LARGE]
    assert model.thinking == "low"
    assert model.provider == "mistral"
    assert model.top_p is None
    assert model.max_context_length is None
    assert model.input_price == 0.0
    assert model.cached_input_price is None
    assert "setting 'temperature'" in caplog.text
    assert "setting 'provider'" in caplog.text
    assert "setting 'top_p'" in caplog.text
    assert "setting 'max_context_length'" in caplog.text
    assert (
        "setting 'input_price' of 'mistral-large-4-0' ignored, invalid" in caplog.text
    )


# Catalog aliases


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "catalog_filter",
    [
        {"hide": ["glm-flash"]},
        {"order": ["glm-flash"]},
        {"default": "glm-flash", "hide": [_LARGE]},
        {"settings": {"glm-flash": {"display_name": "Flash"}}},
    ],
    ids=["hide", "order", "default", "settings"],
)
async def test_a_catalog_alias_names_its_model(
    config_dir: Path, catalog_filter: dict[str, Any]
) -> None:
    """A rule written against a model's old id keeps working once the catalog
    renames the model and publishes the old id as an alias.
    """
    config = await _config(catalog_filter, config_dir=config_dir)

    match next(iter(catalog_filter)):
        case "hide":
            assert _FLASH not in config.models
        case "order":
            assert _catalog_order(config)[0] == _FLASH
        case "default":
            assert config.resolve_default_model_alias() == _FLASH
        case "settings":
            assert config.models[_FLASH].display_name == "Flash"


@pytest.mark.asyncio
async def test_an_id_no_catalog_model_has_is_reported(
    config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        config = await _config(
            {"hide": ["glm-5-3"], "default": "mistral-large-4-0-3"},
            config_dir=config_dir,
        )

    assert _catalog_order(config) == [_LARGE, _GLM, _FLASH, _DEVSTRAL]
    assert "'hide' names 'glm-5-3', which no catalog model has" in caplog.text
    assert (
        "'default' names 'mistral-large-4-0-3', which no catalog model has"
        in caplog.text
    )


# Malformed values and no filter


@pytest.mark.asyncio
async def test_without_the_filter_the_catalog_is_unfiltered(config_dir: Path) -> None:
    config = await _config(None, config_dir=config_dir)

    assert _catalog_order(config) == [_LARGE, _GLM, _FLASH, _DEVSTRAL]
    assert config.resolve_default_model_alias() == _LARGE


@pytest.mark.asyncio
async def test_the_filter_is_read_from_a_json_string(config_dir: Path) -> None:
    config = await _config(
        json.dumps({"hide": [_FLASH], "default": _GLM}), config_dir=config_dir
    )

    assert _FLASH not in config.models
    assert config.resolve_default_model_alias() == _GLM


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog_filter", ["yes", "{not json", [1, 2], 3], ids=str)
async def test_a_value_that_is_not_an_object_leaves_the_catalog_unfiltered(
    catalog_filter: object, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        config = await _config(catalog_filter)

    assert _catalog_order(config) == [_LARGE, _GLM, _FLASH, _DEVSTRAL]
    assert "Model catalog filter: ignored, not a JSON object" in caplog.text


@pytest.mark.asyncio
async def test_a_wrong_typed_key_is_ignored_and_the_others_apply(
    config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        config = await _config({"hide": _FLASH, "default": _GLM}, config_dir=config_dir)

    assert _FLASH in config.models
    assert config.resolve_default_model_alias() == _GLM
    assert "'hide' ignored, not a list" in caplog.text


@pytest.mark.asyncio
async def test_a_non_string_item_is_ignored(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        config = await _config({"order": [_DEVSTRAL, 3]})

    assert _catalog_order(config) == [_DEVSTRAL, _LARGE, _GLM, _FLASH]
    assert "'order' item ignored, not a model id: 3" in caplog.text


@pytest.mark.asyncio
async def test_an_unknown_key_is_ignored_silently(
    config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        config = await _config({"colour": 1, "default": _GLM}, config_dir=config_dir)

    assert config.resolve_default_model_alias() == _GLM
    assert "Model catalog filter" not in caplog.text
    assert config.validation_warnings == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", [{"hide": [_FLASH]}, {"hide": 3}], ids=["well-formed", "malformed"]
)
async def test_a_filter_in_the_users_file_filters_nothing(
    config_dir: Path, value: dict[str, Any]
) -> None:
    config = await _config(None, config_dir=config_dir, model_catalog_filter=value)

    assert _catalog_order(config) == [_LARGE, _GLM, _FLASH, _DEVSTRAL]


@pytest.mark.asyncio
async def test_the_filter_does_not_reach_a_user_setting_check() -> None:
    """The filter rides the GrowthBook layer, never the user's files: it must
    not show as an unknown setting.
    """
    config = await _config({"hide": [_FLASH]})

    assert config.validation_warnings == ()
