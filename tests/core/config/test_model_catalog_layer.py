from __future__ import annotations

import logging
from pathlib import Path
import tomllib
from typing import Any

import pytest
import tomli_w

from vibe.app_server._config_introspect import build_field_wires, collect_layer_values
from vibe.app_server.protocol import ConfigFieldWire
from vibe.config_values import DEFAULT_THINKING_LEVELS
from vibe.core.config import (
    VibeConfigSchema,
    _model_aliases,
    build_default_orchestrator,
)
from vibe.core.config.layers.admin import AdminConfigLayer
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.model_catalog import ModelCatalogLayer, apply_model_catalog
from vibe.core.config.models import ModelRequirement
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.patch import ReplaceOperationPatch
from vibe.core.config.vibe_schema import (
    DEFAULT_ACTIVE_MODEL_CONFIG,
    MEDIUM_3_5_MODEL_CONFIG,
)
from vibe.core.experiments.active import ExperimentName
from vibe.core.model_catalog import (
    ModelCatalogResponse,
    load_cached_model_catalog,
    parse_model_catalog_response,
    store_cached_model_catalog,
)

_GLM = "zai-glm-5-3"
_MEDIUM = "mistral-medium-latest"


@pytest.fixture(autouse=True)
def _fresh_alias_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_model_aliases, "_warned_aliases", set())


def _entry(model_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": model_id,
        "label": model_id.upper(),
        "capabilities": {"function_calling": True},
        **fields,
    }


def _catalog(*entries: Any) -> ModelCatalogResponse:
    parsed = parse_model_catalog_response({"models": list(entries)})
    assert parsed is not None
    return parsed


def _write_user_config(config_dir: Path, **fields: Any) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config.update(fields)
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


async def _orchestrator(
    catalog: ModelCatalogResponse | None,
    *,
    config_dir: Path | None = None,
    **user_fields: Any,
) -> ConfigOrchestrator[VibeConfigSchema]:
    orchestrator = await build_default_orchestrator()
    if catalog is not None:
        await apply_model_catalog(orchestrator, catalog)
    # Written once the catalog is in the stack: a sparse override of a catalog
    # model has no base before that (the orphan case, VIBE-4877).
    if config_dir is not None:
        _write_user_config(config_dir, **user_fields)
        await orchestrator.reload()
    return orchestrator


async def _config(
    catalog: ModelCatalogResponse | None,
    *,
    config_dir: Path | None = None,
    **user_fields: Any,
) -> VibeConfigSchema:
    return (await _orchestrator(catalog, config_dir=config_dir, **user_fields)).config


@pytest.mark.asyncio
async def test_entry_maps_onto_a_mistral_model() -> None:
    config = await _config(
        _catalog(
            _entry(
                _MEDIUM,
                label="Mistral Medium 3.5",
                aliases=["mistral-medium"],
                max_context_length=262144,
                parameters={"temperature": 0.7, "reasoning": ["none", "high"]},
                capabilities={"function_calling": True, "vision": True},
            )
        )
    )

    model = config.models[_MEDIUM]
    assert model.name == _MEDIUM
    assert model.provider == "mistral"
    assert model.display_name == "Mistral Medium 3.5"
    assert model.temperature == 0.7
    assert model.thinking_levels == ["off", "high"]
    assert model.thinking == "high"
    assert model.supports_images is True
    assert model.max_context_length == 262144
    # Unset: the session derives it from the window.
    assert model.auto_compact_threshold is None
    assert config.catalog_model_aliases == {_MEDIUM: ["mistral-medium"]}


@pytest.mark.parametrize(
    ("reasoning", "levels", "thinking"),
    [
        (None, list(DEFAULT_THINKING_LEVELS), "off"),
        ([], ["off"], "off"),
        (["none"], ["off"], "off"),
        (["none", "high"], ["off", "high"], "high"),
        (["low", "high", "max"], ["low", "high", "max"], "high"),
        (["low", "medium"], ["low", "medium"], "medium"),
    ],
)
@pytest.mark.asyncio
async def test_reasoning_becomes_the_offered_thinking_levels(
    reasoning: list[str] | None, levels: list[str], thinking: str
) -> None:
    config = await _config(_catalog(_entry(_GLM, parameters={"reasoning": reasoning})))

    assert config.models[_GLM].thinking_levels == levels
    assert config.models[_GLM].thinking == thinking


@pytest.mark.asyncio
async def test_an_entry_without_reasoning_leaves_the_levels_to_the_known_model() -> (
    None
):
    """*Prepare*: A catalog entry for Mistral Large 2512, which takes no reasoning,
    that lists no reasoning, so the layers fill in the default five levels.
    *Do*: Compile the route facts of the merged model.
    *Assert*: The picker offers off only, from the known-model table, not the
    default five: only a set other than the default says which levels a model
    takes.
    """
    from vibe.app_server._completion_facts import compile_agent_route_facts

    # Prepare
    large = "mistral-large-2512"
    config = await _config(_catalog(_entry(large, parameters={"reasoning": None})))
    model = config.models[large]

    # Do
    facts = compile_agent_route_facts(config.get_provider_for_model(model), model)

    # Assert
    assert model.thinking_levels == list(DEFAULT_THINKING_LEVELS)
    assert facts.thinking_levels == ("off",)


@pytest.mark.asyncio
async def test_reasoning_values_outside_the_standard_levels_are_kept() -> None:
    """*Prepare*: Catalog entries listing levels of the model's own, alone or
    beside a standard one.
    *Do*: Build the config.
    *Assert*: Every level is kept as written, in the catalog's order, and the
    model starts on its set's default. A default the Mistral SDK cannot send
    warns.
    """
    # Do
    config = await _config(
        _catalog(
            _entry(_GLM, parameters={"reasoning": ["minimal", "high"]}),
            _entry(_MEDIUM, parameters={"reasoning": ["ultra"]}),
        )
    )

    # Assert
    assert config.models[_GLM].thinking_levels == ["minimal", "high"]
    assert config.models[_GLM].thinking == "high"
    assert config.models[_MEDIUM].thinking_levels == ["ultra"]
    assert config.models[_MEDIUM].thinking == "ultra"
    assert len(config.validation_warnings) == 1
    assert "cannot send thinking 'ultra'" in config.validation_warnings[0]


@pytest.mark.asyncio
async def test_the_configs_thinking_levels_win_over_the_catalogs(
    config_dir: Path,
) -> None:
    """*Prepare*: A catalog entry listing low and high, and a user config listing
    low and a level of the model's own for the same model, set to that level.
    *Do*: Build the config and compile the model's route.
    *Assert*: The config's levels are the model's and are offered, the catalog's
    are not, and its level runs as written. The Mistral SDK cannot send it, which
    warns.
    """
    from vibe.app_server._completion_facts import compile_agent_route_facts

    # Do
    config = await _config(
        _catalog(_entry(_GLM, parameters={"reasoning": ["low", "high"]})),
        config_dir=config_dir,
        active_model="",
        models=[
            {"name": _GLM, "thinking_levels": ["low", "turbo"], "thinking": "turbo"}
        ],
    )
    model = config.models[_GLM]
    facts = compile_agent_route_facts(config.get_provider_for_model(model), model)

    # Assert
    assert model.thinking_levels == ["low", "turbo"]
    assert facts.thinking_levels == ("low", "turbo")
    assert facts.thinking == "turbo"
    assert len(config.validation_warnings) == 1
    assert "cannot send thinking 'turbo'" in config.validation_warnings[0]


@pytest.mark.asyncio
async def test_null_temperature_keeps_the_field_default() -> None:
    config = await _config(_catalog(_entry(_GLM, parameters={"temperature": None})))

    assert config.models[_GLM].temperature == 1.0


_LARGE = "mistral-large-4-0"


def _large(threshold: object) -> dict[str, Any]:
    return _entry(
        _LARGE,
        max_context_length=1_048_576,
        parameters={"auto_compact_threshold": threshold},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("threshold", "expected"), [(400_000, 400_000), (None, None)])
async def test_the_catalog_threshold_becomes_the_models_own(
    threshold: object, expected: int | None
) -> None:
    # None reaches the session, which derives it from the window.
    config = await _config(_catalog(_large(threshold)))

    assert config.models[_LARGE].auto_compact_threshold == expected


@pytest.mark.asyncio
async def test_a_catalog_threshold_above_the_windows_maximum_is_capped() -> None:
    from vibe.app_server._completion_facts import compile_agent_route_facts

    config = await _config(_catalog(_large(1_048_576)))
    model = config.models[_LARGE]

    facts = compile_agent_route_facts(config.get_provider_for_model(model), model)

    # Kept as sent: the session, not the catalog, fits it into the window.
    assert model.auto_compact_threshold == 1_048_576
    assert facts.compaction_threshold == 1_024_576
    assert facts.threshold_warning == (
        f"Model '{_LARGE}': auto_compact_threshold 1048576 is too high for "
        "max_context_length 1048576 (allowed: 50000 to 1024576), so it was "
        "capped automatically to 1024576. "
        "Set auto_compact_threshold within that range or remove it."
    )


@pytest.mark.asyncio
async def test_the_catalog_threshold_wins_over_a_global_one(config_dir: Path) -> None:
    config = await _config(
        _catalog(_large(400_000)), config_dir=config_dir, auto_compact_threshold=200_000
    )

    assert config.models[_LARGE].auto_compact_threshold == 400_000


@pytest.mark.asyncio
async def test_the_users_model_threshold_wins_over_the_catalogs(
    config_dir: Path,
) -> None:
    config = await _config(
        _catalog(_large(400_000)),
        config_dir=config_dir,
        models=[{"name": _LARGE, "auto_compact_threshold": 200_000}],
    )

    assert config.models[_LARGE].auto_compact_threshold == 200_000


@pytest.mark.asyncio
async def test_the_filters_threshold_wins_over_the_catalogs() -> None:
    orchestrator = await build_default_orchestrator()
    growthbook = orchestrator.get_layer(GrowthbookLayer.NAME)
    assert isinstance(growthbook, GrowthbookLayer)
    growthbook.set_variants({
        ExperimentName.MODEL_CATALOG_FILTER.value: {
            "settings": {_LARGE: {"auto_compact_threshold": 300_000}}
        }
    })
    await apply_model_catalog(orchestrator, _catalog(_large(400_000)))

    assert orchestrator.config.models[_LARGE].auto_compact_threshold == 300_000


async def _threshold_row(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> ConfigFieldWire:
    values = await collect_layer_values(orchestrator.layers, orchestrator.config)
    return next(
        wire
        for wire in build_field_wires(orchestrator.config, values)
        if wire.name == "auto_compact_threshold"
    )


@pytest.mark.asyncio
async def test_the_settings_screen_shows_the_catalog_threshold_as_its_source(
    config_dir: Path,
) -> None:
    orchestrator = await _orchestrator(
        _catalog(_large(400_000)), config_dir=config_dir, active_model=_LARGE
    )
    threshold = await _threshold_row(orchestrator)

    assert threshold.value == 400_000
    assert threshold.origin == ModelCatalogLayer.NAME
    # Editing writes the user's own threshold for this model.
    assert threshold.path == f"/models/{_LARGE}/auto_compact_threshold"


async def _with_admin(
    admin_toml: str, catalog: ModelCatalogResponse
) -> ConfigOrchestrator[VibeConfigSchema]:
    orchestrator = await build_default_orchestrator()
    admin = orchestrator.get_layer(AdminConfigLayer.NAME)
    assert isinstance(admin, AdminConfigLayer)
    admin.load_managed_toml(admin_toml)
    await apply_model_catalog(orchestrator, catalog)
    await orchestrator.set_field("/active_model", _LARGE)
    return orchestrator


@pytest.mark.asyncio
async def test_the_catalog_threshold_wins_over_an_admin_global() -> None:
    orchestrator = await _with_admin(
        "auto_compact_threshold = 200000\n",
        _catalog(_large(400_000), _entry(_GLM, max_context_length=262_144)),
    )

    assert orchestrator.config.models[_LARGE].auto_compact_threshold == 400_000
    # A model without its own threshold takes the admin global.
    assert orchestrator.config.models[_GLM].auto_compact_threshold == 200_000

    threshold = await _threshold_row(orchestrator)
    assert threshold.value == 400_000
    assert threshold.origin == ModelCatalogLayer.NAME
    assert threshold.path == f"/models/{_LARGE}/auto_compact_threshold"


@pytest.mark.asyncio
async def test_an_admin_model_threshold_wins_over_the_catalogs() -> None:
    # The form the managed config accepts: a model entry needs its provider.
    orchestrator = await _with_admin(
        f'[[models]]\nname = "{_LARGE}"\nprovider = "mistral"\n'
        "auto_compact_threshold = 300000\n",
        _catalog(_large(400_000), _entry(_GLM, max_context_length=262_144)),
    )

    large = orchestrator.config.models[_LARGE]
    assert large.auto_compact_threshold == 300_000
    # The catalog's other values for the model stay.
    assert large.max_context_length == 1_048_576
    assert large.display_name == _LARGE.upper()
    assert orchestrator.config.models[_GLM].auto_compact_threshold is None

    threshold = await _threshold_row(orchestrator)
    assert threshold.value == 300_000
    # The CLI reads an admin first layer as read-only.
    assert [(entry.layer, entry.value) for entry in threshold.layer_values] == [
        (AdminConfigLayer.NAME, 300_000),
        (ModelCatalogLayer.NAME, 400_000),
    ]


@pytest.mark.asyncio
async def test_an_admin_model_entry_outlives_the_catalogs() -> None:
    # The managed config's entry is a whole model: it stays available once the
    # catalog stops listing it, until the admin removes it.
    orchestrator = await _with_admin(
        f'[[models]]\nname = "{_LARGE}"\nprovider = "mistral"\n'
        "auto_compact_threshold = 300000\n",
        _catalog(_entry(_GLM)),
    )

    large = orchestrator.config.available_models()[_LARGE]
    assert large.auto_compact_threshold == 300_000
    assert large.max_context_length is None


@pytest.mark.asyncio
async def test_an_admin_global_shows_read_only_on_a_model_without_its_own() -> None:
    orchestrator = await _with_admin(
        "auto_compact_threshold = 200000\n", _catalog(_large(None))
    )

    assert orchestrator.config.models[_LARGE].auto_compact_threshold == 200_000

    threshold = await _threshold_row(orchestrator)
    assert threshold.value == 200_000
    assert threshold.path == "/auto_compact_threshold"
    assert threshold.origin == AdminConfigLayer.NAME


@pytest.mark.asyncio
async def test_null_values_take_the_field_defaults_over_a_built_in_model() -> None:
    builtin = DEFAULT_ACTIVE_MODEL_CONFIG
    config = await _config(_catalog(_entry(builtin.alias)))

    model = config.models[builtin.alias]
    assert model.name == builtin.alias
    assert model.temperature == 1.0
    assert model.thinking_levels == list(DEFAULT_THINKING_LEVELS)
    assert model.thinking == "off"
    assert model.max_context_length is None


@pytest.mark.asyncio
async def test_an_entry_without_function_calling_never_appears() -> None:
    config = await _config(
        _catalog(
            _entry(_GLM, capabilities={"function_calling": False}), _entry(_MEDIUM)
        )
    )

    assert _GLM not in config.models
    assert _MEDIUM in config.models


@pytest.mark.asyncio
async def test_an_entry_the_model_config_rejects_is_skipped() -> None:
    config = await _config(
        _catalog(_entry(_GLM, max_context_length=0), _entry(_MEDIUM))
    )

    assert _GLM not in config.models
    assert _MEDIUM in config.models


@pytest.mark.asyncio
async def test_a_failed_rebuild_keeps_the_previous_catalog() -> None:
    previous = _catalog(_entry(_GLM))
    orchestrator = await _orchestrator(previous)

    async def _failing_reload(**_kwargs: Any) -> None:
        raise RuntimeError("reload failed")

    orchestrator.reload = _failing_reload  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await apply_model_catalog(orchestrator, _catalog(_entry(_MEDIUM)))

    assert _MEDIUM not in orchestrator.config.models
    assert _GLM in orchestrator.config.models


@pytest.mark.asyncio
async def test_a_rejected_catalog_does_not_come_back_on_a_later_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = _catalog(_entry(_GLM))
    orchestrator = await _orchestrator(previous)
    builder = orchestrator._builder
    build = builder.build

    async def _build_then_fail(**kwargs: Any) -> VibeConfigSchema:
        # Fails after every layer, the catalog included, was loaded.
        await build(**kwargs)
        raise ValueError("config rejected")

    monkeypatch.setattr(builder, "build", _build_then_fail)
    with pytest.raises(ValueError):
        await apply_model_catalog(orchestrator, _catalog(_entry(_MEDIUM)))
    monkeypatch.setattr(builder, "build", build)

    orchestrator.rebuild()

    assert _MEDIUM not in orchestrator.config.models
    assert _GLM in orchestrator.config.models
    assert _MEDIUM not in orchestrator.config.models


@pytest.mark.asyncio
async def test_a_malformed_entry_is_skipped_and_the_rest_merges() -> None:
    config = await _config(_catalog({"id": _GLM}, _entry(_MEDIUM)))

    assert _GLM not in config.models
    assert _MEDIUM in config.models


@pytest.mark.parametrize("catalog", [None, _catalog()])
@pytest.mark.asyncio
async def test_no_catalog_leaves_the_config_unchanged(
    catalog: ModelCatalogResponse | None,
) -> None:
    baseline = (await build_default_orchestrator()).config

    config = await _config(catalog)

    assert config.models == baseline.models
    assert config.catalog_model_aliases == {}


@pytest.mark.asyncio
async def test_a_local_sparse_override_keeps_the_other_catalog_fields(
    config_dir: Path,
) -> None:
    config = await _config(
        _catalog(
            _entry(
                _GLM,
                label="GLM 5.3",
                parameters={"temperature": 1.0, "reasoning": ["low", "high"]},
            )
        ),
        config_dir=config_dir,
        # Unpinned: writing models replaces the fixture's devstral entry, and
        # its pin would warn for a reason this test is not about.
        active_model="",
        models=[{"name": _GLM, "temperature": 0.9}],
    )

    model = config.models[_GLM]
    assert model.temperature == 0.9
    assert model.display_name == "GLM 5.3"
    assert model.thinking_levels == ["low", "high"]
    assert config.validation_warnings == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "catalog",
    [_catalog(), _catalog(_entry(_GLM, capabilities={"function_calling": False}))],
    ids=["empty", "no-usable-entry"],
)
async def test_a_catalog_with_no_model_leaves_the_built_in_models(
    catalog: ModelCatalogResponse,
) -> None:
    config = await _config(catalog)

    assert DEFAULT_ACTIVE_MODEL_CONFIG.alias in config.models
    assert config.resolve_default_model_alias() == DEFAULT_ACTIVE_MODEL_CONFIG.alias


@pytest.mark.asyncio
async def test_admin_wins_over_local_config_and_the_catalog(config_dir: Path) -> None:
    orchestrator = await build_default_orchestrator()
    admin = orchestrator.get_layer(AdminConfigLayer.NAME)
    assert isinstance(admin, AdminConfigLayer)
    admin.restore({"models": {_GLM: {"temperature": 0.1}}})
    await apply_model_catalog(
        orchestrator, _catalog(_entry(_GLM, parameters={"temperature": 1.0}))
    )
    _write_user_config(config_dir, models=[{"name": _GLM, "temperature": 0.9}])

    await orchestrator.reload()

    assert orchestrator.config.models[_GLM].temperature == 0.1


@pytest.mark.asyncio
async def test_a_pin_on_an_alias_selects_the_catalog_model(config_dir: Path) -> None:
    # The user picked the model when it was called "glm-old".
    _write_user_config(config_dir, active_model="glm-old")

    config = await _config(_catalog(_entry(_GLM, aliases=["glm-old"])))

    assert config.active_model == _GLM
    assert config.get_active_model().name == _GLM
    assert config.validation_warnings == ()


@pytest.mark.asyncio
async def test_a_full_local_model_keyed_on_an_alias_keeps_its_identity(
    config_dir: Path,
) -> None:
    _write_user_config(
        config_dir,
        active_model="glm",
        models=[{"name": "local-glm", "provider": "llamacpp", "alias": "glm"}],
    )

    config = await _config(_catalog(_entry(_GLM, aliases=["glm"])))

    assert config.models[_GLM].provider == "mistral"
    assert config.get_active_model().provider == "llamacpp"


@pytest.mark.asyncio
async def test_an_alias_claimed_twice_selects_neither_model(
    config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_user_config(config_dir, active_model="shared")

    with caplog.at_level(logging.WARNING):
        config = await _config(
            _catalog(
                _entry(_GLM, aliases=["shared", "glm"]),
                _entry(_MEDIUM, aliases=["shared"]),
            )
        )

    assert {_GLM, _MEDIUM} <= set(config.models)
    assert config.active_model != _GLM
    assert config.active_model != _MEDIUM
    assert "Model alias 'shared' is ignored" in caplog.text


@pytest.mark.asyncio
async def test_a_blank_alias_never_captures_the_unpinned_default(
    config_dir: Path,
) -> None:
    config = await _config(
        _catalog(_entry(_GLM, aliases=["", " "])),
        config_dir=config_dir,
        active_model="",
    )

    assert config.active_model == ""


@pytest.mark.asyncio
async def test_an_ignored_alias_is_logged_once_across_rebuilds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        orchestrator = await _orchestrator(
            _catalog(
                _entry(_GLM, aliases=["shared"]), _entry(_MEDIUM, aliases=["shared"])
            )
        )
        await orchestrator.reload()
        await orchestrator.reload()

    assert caplog.text.count("Model alias 'shared' is ignored") == 1


@pytest.mark.asyncio
async def test_catalog_models_replace_the_built_in_default(
    config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """VIBE-5548: with catalog models available, the built-in default is not
    available anymore, and a catalog alias takes its name: a pin on the
    built-in's name runs the catalog model.
    """
    builtin = DEFAULT_ACTIVE_MODEL_CONFIG.alias
    _write_user_config(config_dir, active_model=builtin)

    with caplog.at_level(logging.WARNING):
        config = await _config(_catalog(_entry(_MEDIUM, aliases=[builtin])))

    assert builtin not in config.models
    assert _MEDIUM in config.models
    assert config.models[_MEDIUM].display_name == _MEDIUM.upper()
    # The pin on the built-in's name selects the catalog model.
    assert config.get_active_model().alias == _MEDIUM
    assert f"Model alias '{builtin}' is ignored" not in caplog.text


@pytest.mark.asyncio
async def test_a_settings_entry_under_the_built_in_name_applies_to_the_catalog_model(
    config_dir: Path,
) -> None:
    builtin = DEFAULT_ACTIVE_MODEL_CONFIG.alias
    config = await _config(
        _catalog(_entry(_MEDIUM, aliases=[builtin])),
        config_dir=config_dir,
        active_model="",
        models={builtin: {"temperature": 0.7}},
    )

    assert builtin not in config.models
    assert config.models[_MEDIUM].temperature == 0.7


@pytest.mark.asyncio
async def test_a_user_defined_model_under_the_built_in_name_is_kept(
    config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A whole model the user defines under the built-in's name keeps it: the
    catalog alias is then ignored and the user's model wins the name.
    """
    builtin = DEFAULT_ACTIVE_MODEL_CONFIG.alias
    with caplog.at_level(logging.WARNING):
        config = await _config(
            _catalog(_entry(_MEDIUM, aliases=[builtin])),
            config_dir=config_dir,
            active_model="",
            models=[{"name": "my-medium", "provider": "llamacpp", "alias": builtin}],
        )

    assert config.models[builtin].name == "my-medium"
    assert _MEDIUM in config.models
    assert f"Model alias '{builtin}' is ignored" in caplog.text


@pytest.mark.asyncio
async def test_an_empty_catalog_keeps_the_built_in_default() -> None:
    # Every entry dropped (no function calling): no catalog model is available,
    # so the built-in default stays.
    config = await _config(
        _catalog(_entry(_GLM, capabilities={"function_calling": False}))
    )

    assert DEFAULT_ACTIVE_MODEL_CONFIG.alias in config.models
    assert config.resolve_default_model_alias() == DEFAULT_ACTIVE_MODEL_CONFIG.alias


@pytest.mark.asyncio
async def test_allowed_models_filters_catalog_models(config_dir: Path) -> None:
    _write_user_config(
        config_dir, active_model=_GLM, allowed_models=["mistral-vibe-cli-*"]
    )

    config = await _config(_catalog(_entry(_GLM)))

    assert _GLM in config.models
    assert _GLM not in config.available_models()
    assert config.get_active_model().alias == config.resolve_default_model_alias()
    assert config.get_active_model().name.startswith("mistral-vibe-cli-")


@pytest.mark.asyncio
async def test_a_hot_swap_keeps_the_pin_and_takes_the_fresh_values(
    config_dir: Path,
) -> None:
    orchestrator = await _orchestrator(
        _catalog(_entry(_GLM, parameters={"temperature": 1.0})),
        config_dir=config_dir,
        active_model=_GLM,
    )

    await apply_model_catalog(
        orchestrator,
        _catalog(_entry(_GLM, parameters={"temperature": 0.5}), _entry(_MEDIUM)),
    )

    assert orchestrator.config.active_model == _GLM
    assert orchestrator.config.get_active_model().temperature == 0.5
    assert _MEDIUM in orchestrator.config.models


@pytest.mark.asyncio
async def test_reapplying_an_equal_catalog_skips_the_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = await build_default_orchestrator()
    assert await apply_model_catalog(orchestrator, _catalog(_entry(_GLM)))
    reloads = 0
    reload = orchestrator.reload

    async def _counting_reload(**kwargs: Any) -> None:
        nonlocal reloads
        reloads += 1
        await reload(**kwargs)

    monkeypatch.setattr(orchestrator, "reload", _counting_reload)

    # A fresh parse of the same payload, as a fetch returning the cache.
    assert not await apply_model_catalog(orchestrator, _catalog(_entry(_GLM)))
    assert reloads == 0


@pytest.mark.asyncio
async def test_the_recommendation_is_the_unpinned_default(config_dir: Path) -> None:
    _write_user_config(config_dir, active_model="")

    config = await _config(_catalog(_entry(_GLM), _entry(_MEDIUM, recommended=True)))

    assert config.resolve_default_model_alias() == _MEDIUM
    assert config.get_active_model().alias == _MEDIUM


@pytest.mark.asyncio
async def test_allowed_models_match_the_retired_built_in_names_via_catalog_aliases(
    config_dir: Path,
) -> None:
    """The built-in's names (alias and wire name) are published as aliases of
    its catalog replacement, so an allowlist written against the built-in
    keeps matching instead of degrading to allow-all.
    """
    builtin = MEDIUM_3_5_MODEL_CONFIG
    _write_user_config(config_dir, allowed_models=["mistral-vibe-cli-*"])

    config = await _config(
        _catalog(
            _entry(_MEDIUM, aliases=[builtin.alias, builtin.name], recommended=True)
        )
    )

    assert builtin.alias not in config.models
    # The glob still restricts: only matching models stay available (the
    # fixture's user model devstral-latest has the same wire name, plus the
    # catalog replacement via its alias) — no degrade to allow-all.
    assert _MEDIUM in config.available_models()
    assert set(config.available_models()) == {"devstral-latest", _MEDIUM}


@pytest.mark.asyncio
async def test_admin_allowed_models_matching_a_retired_built_in_do_not_fail_closed() -> (
    None
):
    builtin = MEDIUM_3_5_MODEL_CONFIG
    orchestrator = await build_default_orchestrator()
    admin = orchestrator.get_layer(AdminConfigLayer.NAME)
    assert isinstance(admin, AdminConfigLayer)
    admin.restore({"allowed_models": ["mistral-vibe-cli-*"]})

    await apply_model_catalog(
        orchestrator, _catalog(_entry(_MEDIUM, aliases=[builtin.alias, builtin.name]))
    )

    # The config builds (no fail-closed) and the catalog replacement is allowed.
    assert _MEDIUM in orchestrator.config.available_models()


@pytest.mark.asyncio
async def test_allowed_models_cannot_bring_the_built_in_default_back(
    config_dir: Path,
) -> None:
    """VIBE-5548: a filter that used to fall back to the built-in default now
    falls back within the catalog, which replaced it.
    """
    _write_user_config(config_dir, allowed_models=["mistral-vibe-cli-*"])

    config = await _config(_catalog(_entry(_MEDIUM, recommended=True)))

    assert DEFAULT_ACTIVE_MODEL_CONFIG.alias not in config.models
    assert DEFAULT_ACTIVE_MODEL_CONFIG.alias not in config.available_models()
    assert config.resolve_default_model_alias() != DEFAULT_ACTIVE_MODEL_CONFIG.alias


@pytest.mark.asyncio
async def test_a_dropped_recommended_entry_falls_back_to_a_catalog_model() -> None:
    config = await _config(
        _catalog(
            _entry(_GLM),
            _entry(_MEDIUM, recommended=True, capabilities={"function_calling": False}),
        )
    )

    assert config.catalog_recommended_model == ""
    assert DEFAULT_ACTIVE_MODEL_CONFIG.alias not in config.models
    assert config.resolve_default_model_alias() == _GLM


@pytest.mark.asyncio
async def test_a_catalog_listing_large_4_shows_one_row_for_the_old_pin(
    config_dir: Path,
) -> None:
    _write_user_config(config_dir, active_model="mistral-large-4-0-3")

    config = await _config(
        _catalog(
            _entry(
                "mistral-large-4-0",
                aliases=["mistral-large-4-0", "mistral-large-4-0-3", "mistral-large-4"],
                recommended=True,
            ),
            _entry(_MEDIUM),
        )
    )

    assert [
        model.alias
        for model in config.available_models().values()
        if model.name == "mistral-large-4-0"
    ] == ["mistral-large-4-0"]
    assert config.get_active_model().alias == "mistral-large-4-0"
    assert config.validation_warnings == ()


@pytest.mark.asyncio
async def test_an_old_large_4_pin_without_catalog_or_growthbook_runs_large_4(
    config_dir: Path,
) -> None:
    _write_user_config(config_dir, active_model="mistral-large-4-0-3")

    config = await _config(None)

    assert config.get_active_model().alias == DEFAULT_ACTIVE_MODEL_CONFIG.alias
    assert config.validation_warnings == (
        "Model 'mistral-large-4-0-3' is not available — running 'Mistral Large 4'. "
        "Pick another model to stop this notice.",
    )


@pytest.mark.asyncio
async def test_without_a_catalog_the_default_is_unchanged() -> None:
    config = await _config(None)

    assert config.resolve_default_model_alias() == DEFAULT_ACTIVE_MODEL_CONFIG.alias


@pytest.mark.asyncio
async def test_a_missing_pin_runs_the_default_and_names_no_cause(
    config_dir: Path,
) -> None:
    _write_user_config(config_dir, active_model="glm-5-3")

    config = await _config(
        _catalog(_entry(_MEDIUM, label="Mistral Medium", recommended=True))
    )

    assert config.get_active_model().alias == _MEDIUM
    assert config.validation_warnings == (
        "Model 'glm-5-3' is not available — running 'Mistral Medium'. "
        "Pick another model to stop this notice.",
    )
    stored = tomllib.loads((config_dir / "config.toml").read_text(encoding="utf-8"))
    assert stored["active_model"] == "glm-5-3"


@pytest.mark.asyncio
async def test_a_pin_on_the_built_in_name_without_a_catalog_alias_falls_back(
    config_dir: Path,
) -> None:
    """VIBE-5548: once the catalog lists models the built-in is gone; a pin on
    its name that no catalog alias claims gets the missing-model fallback.
    """
    builtin = DEFAULT_ACTIVE_MODEL_CONFIG.alias
    _write_user_config(config_dir, active_model=builtin)

    config = await _config(
        _catalog(_entry(_MEDIUM, label="Mistral Medium", recommended=True))
    )

    assert builtin not in config.models
    assert config.get_active_model().alias == _MEDIUM
    assert any(
        f"Model '{builtin}' is not available" in warning
        for warning in config.validation_warnings
    )


@pytest.mark.asyncio
async def test_the_missing_pin_notice_returns_each_session_until_a_new_pick(
    config_dir: Path,
) -> None:
    _write_user_config(config_dir, active_model="glm-5-3")
    catalog = _catalog(_entry(_MEDIUM, recommended=True))

    first_session = await _orchestrator(catalog)
    second_session = await _orchestrator(catalog)
    assert first_session.config.validation_warnings
    assert second_session.config.validation_warnings

    await second_session.apply_patch(
        [ReplaceOperationPatch(path="/active_model", value=_MEDIUM)], reason="pick"
    )
    next_session = await _orchestrator(catalog)

    assert next_session.config.get_active_model().alias == _MEDIUM
    assert next_session.config.validation_warnings == ()


@pytest.mark.asyncio
async def test_a_pinned_model_back_in_the_catalog_runs_silently(
    config_dir: Path,
) -> None:
    _write_user_config(config_dir, active_model=_GLM)
    orchestrator = await _orchestrator(_catalog(_entry(_MEDIUM)))
    assert orchestrator.config.validation_warnings

    await apply_model_catalog(orchestrator, _catalog(_entry(_GLM), _entry(_MEDIUM)))

    assert orchestrator.config.get_active_model().alias == _GLM
    assert orchestrator.config.validation_warnings == ()


@pytest.mark.parametrize(
    "override",
    [
        pytest.param([{"name": _GLM, "temperature": 0.9}], id="models-list"),
        pytest.param({_GLM: {"temperature": 0.9}}, id="models-table"),
    ],
)
@pytest.mark.asyncio
async def test_an_override_of_a_missing_model_waits_for_the_model(
    config_dir: Path, override: Any
) -> None:
    _write_user_config(config_dir, active_model="", models=override)

    orchestrator = await build_default_orchestrator()

    assert _GLM not in orchestrator.config.models
    assert orchestrator.config.validation_warnings == (
        f"Settings for model '{_GLM}' are ignored: no such model is available.",
    )
    await apply_model_catalog(orchestrator, _catalog(_entry(_GLM)))
    assert orchestrator.config.models[_GLM].temperature == 0.9
    assert orchestrator.config.validation_warnings == ()


@pytest.mark.asyncio
async def test_a_provider_only_override_of_a_missing_model_waits_for_the_model(
    config_dir: Path,
) -> None:
    # A field-only write can persist provider alone; with no name the entry
    # cannot validate once its model disappears, so it warns and waits.
    _write_user_config(config_dir, active_model="", models={_GLM: {"provider": "zai"}})

    orchestrator = await build_default_orchestrator()

    assert _GLM not in orchestrator.config.models
    assert orchestrator.config.validation_warnings == (
        f"Settings for model '{_GLM}' are ignored: no such model is available.",
    )
    await apply_model_catalog(orchestrator, _catalog(_entry(_GLM)))
    assert orchestrator.config.models[_GLM].provider == "zai"
    assert orchestrator.config.validation_warnings == ()


@pytest.mark.asyncio
async def test_a_pin_and_an_override_of_the_same_missing_model_warn_once(
    config_dir: Path,
) -> None:
    _write_user_config(
        config_dir, active_model=_GLM, models={_GLM: {"temperature": 0.9}}
    )

    config = await _config(None)

    assert config.validation_warnings == (
        f"Model '{_GLM}' is not available — running 'Mistral Large 4'. "
        "Pick another model to stop this notice.",
    )


@pytest.mark.asyncio
async def test_a_pin_and_an_override_of_two_missing_models_warn_for_each(
    config_dir: Path,
) -> None:
    _write_user_config(
        config_dir, active_model=_GLM, models={"ghost-model": {"temperature": 0.9}}
    )

    config = await _config(None)

    assert config.validation_warnings == (
        f"Model '{_GLM}' is not available — running 'Mistral Large 4'. "
        "Pick another model to stop this notice.",
        "Settings for model 'ghost-model' are ignored: no such model is available.",
    )


@pytest.mark.asyncio
async def test_settings_keyed_on_a_catalog_alias_apply_to_its_model(
    config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_user_config(
        config_dir, active_model="glm-old", models={"glm-old": {"temperature": 0.9}}
    )

    with caplog.at_level(logging.INFO):
        config = await _config(_catalog(_entry(_GLM, aliases=["glm-old"])))

    assert config.get_active_model().alias == _GLM
    assert config.models[_GLM].temperature == 0.9
    assert "glm-old" not in config.models
    assert config.validation_warnings == ()
    assert "Model settings 'glm-old' apply to model 'zai-glm-5-3'" in caplog.text


_LOCKED = {"kind": "higher_plan"}
_UNKNOWN = {"kind": "unknown_kind"}
_GATEWAY = "internal-ai-gateway"


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"requires": {"kind": "unknown_kind"}}, "requires {'kind': 'unknown_kind'}"),
        ({"requires": "yes"}, "requires 'yes'"),
        ({"requires": []}, "requires []"),
        ({"requires": {}}, "requires {}"),
        ({"requires": False}, "requires False"),
        ({"requires": 0}, "requires 0"),
        ({"provider": _GATEWAY}, "provider 'internal-ai-gateway'"),
        ({"provider": "Mistral"}, "provider 'Mistral'"),
        ({"provider": ""}, "provider ''"),
        ({"provider": 123}, "provider 123"),
        (
            {"requires": {"kind": "unknown_kind"}, "provider": _GATEWAY},
            "requires {'kind': 'unknown_kind'}, provider 'internal-ai-gateway'",
        ),
        ({"requires": _LOCKED, "provider": _GATEWAY}, "provider 'internal-ai-gateway'"),
    ],
)
@pytest.mark.asyncio
async def test_an_entry_this_client_cannot_run_is_hidden_and_logged(
    fields: dict[str, Any], reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        config = await _config(_catalog(_entry(_GLM, **fields), _entry(_MEDIUM)))

    assert _GLM not in config.models
    assert _MEDIUM in config.models
    assert f"Model catalog entry '{_GLM}' skipped: {reason}" in caplog.messages


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"requires": None},
        {"provider": None},
        {"provider": "mistral"},
        {"provider": "mistral", "requires": None},
    ],
)
@pytest.mark.asyncio
async def test_an_entry_mistral_serves_without_requirement_is_shown(
    fields: dict[str, Any],
) -> None:
    config = await _config(_catalog(_entry(_GLM, **fields)))

    assert _GLM in config.available_models()


@pytest.mark.asyncio
async def test_the_alias_of_a_hidden_entry_does_not_resolve(config_dir: Path) -> None:
    _write_user_config(config_dir, active_model="glm-old")

    config = await _config(
        _catalog(
            _entry(_GLM, aliases=["glm-old"], requires=_UNKNOWN),
            _entry(_MEDIUM, label="Mistral Medium", recommended=True),
        )
    )

    assert _GLM not in config.catalog_model_aliases
    assert config.get_active_model().alias == _MEDIUM
    assert config.validation_warnings == (
        "Model 'glm-old' is not available — running 'Mistral Medium'. "
        "Pick another model to stop this notice.",
    )


@pytest.mark.asyncio
async def test_a_hidden_recommended_entry_leaves_the_default_as_with_no_recommendation() -> (
    None
):
    hidden = await _config(
        _catalog(_entry(_GLM), _entry(_MEDIUM, recommended=True, provider=_GATEWAY))
    )
    unrecommended = await _config(_catalog(_entry(_GLM)))

    assert hidden.catalog_recommended_model == ""
    assert hidden.resolve_default_model_alias() == (
        unrecommended.resolve_default_model_alias()
    )


@pytest.mark.asyncio
async def test_a_pin_on_a_hidden_entry_runs_the_default_with_the_notice(
    config_dir: Path,
) -> None:
    _write_user_config(config_dir, active_model=_GLM)

    config = await _config(
        _catalog(
            _entry(_GLM, requires=_UNKNOWN),
            _entry(_MEDIUM, label="Mistral Medium", recommended=True),
        )
    )

    assert config.get_active_model().alias == _MEDIUM
    assert config.validation_warnings == (
        f"Model '{_GLM}' is not available — running 'Mistral Medium'. "
        "Pick another model to stop this notice.",
    )


@pytest.mark.asyncio
async def test_an_entry_locked_behind_a_higher_plan_is_listed_locked() -> None:
    config = await _config(
        _catalog(_entry(_GLM, label="GLM 5.3", requires=_LOCKED), _entry(_MEDIUM))
    )

    assert config.available_models()[_GLM].requires == ModelRequirement(
        kind="higher_plan"
    )
    assert config.available_models()[_MEDIUM].requires is None


@pytest.mark.asyncio
async def test_a_higher_plan_requirement_with_more_fields_still_locks() -> None:
    config = await _config(
        _catalog(_entry(_GLM, requires={"kind": "higher_plan", "plan": "pro"}))
    )

    assert config.models[_GLM].requires == ModelRequirement(kind="higher_plan")


@pytest.mark.asyncio
async def test_a_locked_recommended_entry_is_never_the_default() -> None:
    config = await _config(
        _catalog(
            _entry(_GLM, requires=_LOCKED, recommended=True),
            _entry("zai-glm-5-3-flash"),
            _entry(_MEDIUM),
        )
    )

    assert config.catalog_recommended_model == _GLM
    assert config.resolve_default_model_alias() == "zai-glm-5-3-flash"


@pytest.mark.asyncio
async def test_a_locked_entry_the_filter_picks_is_never_the_default() -> None:
    orchestrator = await _orchestrator(
        _catalog(_entry(_GLM, requires=_LOCKED), _entry(_MEDIUM, recommended=True))
    )
    layer = orchestrator.get_layer(GrowthbookLayer.NAME)
    assert isinstance(layer, GrowthbookLayer)
    layer.set_variants({ExperimentName.MODEL_CATALOG_FILTER.value: {"default": _GLM}})

    await orchestrator.reload()

    assert orchestrator.config.catalog_default_model == _GLM
    assert orchestrator.config.resolve_default_model_alias() == _MEDIUM


@pytest.mark.asyncio
async def test_a_pin_on_a_locked_entry_stays_pinned(config_dir: Path) -> None:
    config = await _config(
        _catalog(_entry(_GLM, requires=_LOCKED), _entry(_MEDIUM, recommended=True)),
        config_dir=config_dir,
        active_model=_GLM,
    )

    assert config.get_active_model().alias == _GLM
    assert config.get_active_model().requires is not None
    assert config.validation_warnings == ()


@pytest.mark.asyncio
async def test_a_cached_catalog_keeps_its_locked_entries() -> None:
    fetched = _catalog(
        _entry(_GLM, requires=_LOCKED),
        _entry("zai-glm-5-3-flash", requires=_UNKNOWN),
        _entry(_MEDIUM),
    )
    store_cached_model_catalog("user-abc", fetched)
    record = load_cached_model_catalog("user-abc")
    assert record is not None

    from_cache = await _config(record[0])
    from_fetch = await _config(fetched)

    assert from_cache.models[_GLM].requires == ModelRequirement(kind="higher_plan")
    assert "zai-glm-5-3-flash" not in from_cache.models
    assert from_cache.models == from_fetch.models
