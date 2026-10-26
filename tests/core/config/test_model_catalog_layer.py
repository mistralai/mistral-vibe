from __future__ import annotations

import json
import logging
from pathlib import Path
import tomllib
from typing import Any

import pytest
import tomli_w

from vibe.config_values import DEFAULT_THINKING_LEVELS
from vibe.core.config import (
    VibeConfigSchema,
    _model_aliases,
    build_default_orchestrator,
)
from vibe.core.config.layers.admin import AdminConfigLayer
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.model_catalog import apply_model_catalog
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.patch import ReplaceOperationPatch
from vibe.core.config.vibe_schema import DEFAULT_ACTIVE_MODEL_CONFIG
from vibe.core.experiments.active import ExperimentName
from vibe.core.model_catalog import ModelCatalogResponse, parse_model_catalog_response

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
async def test_unknown_reasoning_values_are_dropped_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        config = await _config(
            _catalog(
                _entry(_GLM, parameters={"reasoning": ["minimal", "high"]}),
                _entry(_MEDIUM, parameters={"reasoning": ["ultra"]}),
            )
        )

    assert config.models[_GLM].thinking_levels == ["high"]
    # No known value left: the off-only set, like an empty list.
    assert config.models[_MEDIUM].thinking_levels == ["off"]
    assert "unknown reasoning value 'minimal'" in caplog.text


@pytest.mark.asyncio
async def test_null_temperature_keeps_the_field_default() -> None:
    config = await _config(_catalog(_entry(_GLM, parameters={"temperature": None})))

    assert config.models[_GLM].temperature == 0.2


@pytest.mark.asyncio
async def test_null_values_take_the_field_defaults_over_a_built_in_model() -> None:
    builtin = DEFAULT_ACTIVE_MODEL_CONFIG
    config = await _config(_catalog(_entry(builtin.alias)))

    model = config.models[builtin.alias]
    assert model.name == builtin.alias
    assert model.temperature == 0.2
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
async def test_growthbook_wins_over_the_catalog() -> None:
    orchestrator = await build_default_orchestrator()
    growthbook = orchestrator.get_layer(GrowthbookLayer.NAME)
    assert isinstance(growthbook, GrowthbookLayer)
    growthbook.set_variants({
        ExperimentName.CLI_EXTRA_MODELS.value: json.dumps([
            {"name": _GLM, "provider": "mistral", "alias": _GLM, "temperature": 0.3}
        ])
    })

    await apply_model_catalog(
        orchestrator, _catalog(_entry(_GLM, parameters={"temperature": 1.0}))
    )

    assert orchestrator.config.models[_GLM].temperature == 0.3


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
async def test_the_built_in_default_stays_beside_a_catalog_entry_claiming_its_id(
    config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    builtin = DEFAULT_ACTIVE_MODEL_CONFIG.alias
    _write_user_config(config_dir, active_model=builtin)

    with caplog.at_level(logging.WARNING):
        config = await _config(_catalog(_entry(_MEDIUM, aliases=[builtin])))

    assert config.models[builtin].name == DEFAULT_ACTIVE_MODEL_CONFIG.name
    assert _MEDIUM in config.models
    assert config.get_active_model().alias == builtin
    assert f"Model alias '{builtin}' is ignored" in caplog.text


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
async def test_a_live_routed_default_wins_over_the_recommendation() -> None:
    orchestrator = await build_default_orchestrator()
    growthbook = orchestrator.get_layer(GrowthbookLayer.NAME)
    assert isinstance(growthbook, GrowthbookLayer)
    growthbook.set_variants({
        ExperimentName.CLI_MODEL_ROUTING.value: json.dumps({
            "active_model": DEFAULT_ACTIVE_MODEL_CONFIG.alias
        })
    })

    await apply_model_catalog(orchestrator, _catalog(_entry(_MEDIUM, recommended=True)))

    assert (
        orchestrator.config.resolve_default_model_alias()
        == DEFAULT_ACTIVE_MODEL_CONFIG.alias
    )


@pytest.mark.asyncio
async def test_a_recommendation_allowed_models_excludes_leaves_the_local_default(
    config_dir: Path,
) -> None:
    _write_user_config(config_dir, allowed_models=["mistral-vibe-cli-*"])

    config = await _config(_catalog(_entry(_MEDIUM, recommended=True)))

    assert config.resolve_default_model_alias() == DEFAULT_ACTIVE_MODEL_CONFIG.alias


@pytest.mark.asyncio
async def test_a_dropped_recommended_entry_leaves_the_local_default() -> None:
    config = await _config(
        _catalog(
            _entry(_GLM),
            _entry(_MEDIUM, recommended=True, capabilities={"function_calling": False}),
        )
    )

    assert config.catalog_recommended_model == ""
    assert config.resolve_default_model_alias() == DEFAULT_ACTIVE_MODEL_CONFIG.alias


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
        "Pick a model with /model to stop this notice.",
    )
    stored = tomllib.loads((config_dir / "config.toml").read_text(encoding="utf-8"))
    assert stored["active_model"] == "glm-5-3"


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
        f"Model '{_GLM}' is not available — running 'Mistral Medium 3.5'. "
        "Pick a model with /model to stop this notice.",
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
        f"Model '{_GLM}' is not available — running 'Mistral Medium 3.5'. "
        "Pick a model with /model to stop this notice.",
        "Settings for model 'ghost-model' are ignored: no such model is available.",
    )


@pytest.mark.asyncio
async def test_an_override_keyed_on_a_resolved_alias_keeps_its_warning(
    config_dir: Path,
) -> None:
    # The pin resolves through the alias, so no fallback fires; the override
    # keyed on the alias string names no model and must not lose its notice.
    _write_user_config(
        config_dir, active_model="glm-old", models={"glm-old": {"temperature": 0.9}}
    )

    config = await _config(_catalog(_entry(_GLM, aliases=["glm-old"])))

    assert config.get_active_model().alias == _GLM
    assert config.validation_warnings == (
        "Settings for model 'glm-old' are ignored: no such model is available.",
    )
