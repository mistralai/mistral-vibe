"""A catalog alias names its model wherever a model is named.

The catalog lists ``zai-glm-5-3`` with the old name ``glm-5-3`` in its aliases.
"""

from __future__ import annotations

import logging
from pathlib import Path
import tomllib
from typing import Any

import pytest
import tomli_w

from vibe.app_server._config_introspect import build_field_wires, collect_layer_values
from vibe.app_server._config_write import (
    config_write_ops_to_patches,
    model_config_write_ops,
)
from vibe.app_server._session_model import model_thinking_is_session_writable
from vibe.core.agents.registry import apply_profile_overrides
from vibe.core.config import (
    VibeConfigSchema,
    _model_aliases,
    build_default_orchestrator,
)
from vibe.core.config.layers.model_catalog import apply_model_catalog
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.models import UtilityFeature
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.model_catalog import ModelCatalogResponse, parse_model_catalog_response

_GLM = "zai-glm-5-3"
_OLD = "glm-5-3"


@pytest.fixture(autouse=True)
def _fresh_alias_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_model_aliases, "_warned_aliases", set())
    monkeypatch.setattr(_model_aliases, "_reported_settings", set())


def _catalog(*aliases: str) -> ModelCatalogResponse:
    parsed = parse_model_catalog_response({
        "models": [
            {
                "id": _GLM,
                "label": "GLM 5.3",
                "aliases": list(aliases),
                "capabilities": {"function_calling": True},
            }
        ]
    })
    assert parsed is not None
    return parsed


def _write_user_config(config_dir: Path, **fields: Any) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    # The base test config pins a model no layer here defines.
    config["active_model"] = ""
    config.update(fields)
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


def _user_active_model(config_dir: Path) -> Any:
    config = tomllib.loads((config_dir / "config.toml").read_text(encoding="utf-8"))
    return config.get("active_model")


def _user_models(config_dir: Path) -> dict[str, Any]:
    config = tomllib.loads((config_dir / "config.toml").read_text(encoding="utf-8"))
    models = config.get("models", [])
    if isinstance(models, dict):
        return models
    return {entry["alias"]: entry for entry in models}


async def _orchestrator(
    config_dir: Path,
    *,
    aliases: tuple[str, ...] = (_OLD,),
    with_catalog: bool = True,
    **user_fields: Any,
) -> ConfigOrchestrator[VibeConfigSchema]:
    orchestrator = await build_default_orchestrator()
    if with_catalog:
        await apply_model_catalog(orchestrator, _catalog(*aliases))
    _write_user_config(config_dir, **user_fields)
    await orchestrator.reload()
    return orchestrator


async def _write(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    *,
    model_alias: str | None = None,
    reasoning_effort: str | None = None,
) -> None:
    config = orchestrator.config
    ops = model_config_write_ops(
        config, model_alias=model_alias, reasoning_effort=reasoning_effort
    )
    failures = await orchestrator.apply_patch(
        config_write_ops_to_patches(
            config,
            ops,
            sparse_override_model_aliases=(
                await orchestrator.sparse_override_model_aliases()
            ),
        ),
        reason="test model pick",
    )
    assert failures == []


@pytest.mark.asyncio
async def test_settings_under_an_old_name_apply_to_the_catalog_model(
    config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        orchestrator = await _orchestrator(
            config_dir, models={_OLD: {"temperature": 0.7}}
        )
        await orchestrator.reload()

    config = orchestrator.config
    assert config.models[_GLM].temperature == 0.7
    assert config.models[_GLM].display_name == "GLM 5.3"
    assert _OLD not in config.models
    assert config.validation_warnings == ()
    # Logged, once however often the config is rebuilt.
    assert caplog.text.count(f"Model settings '{_OLD}' apply to model '{_GLM}'") == 1


@pytest.mark.asyncio
async def test_the_catalog_id_entry_wins_over_the_old_name(config_dir: Path) -> None:
    config = (
        await _orchestrator(
            config_dir,
            models={
                _OLD: {"temperature": 0.7, "max_output_tokens": 1234},
                _GLM: {"temperature": 0.9},
            },
        )
    ).config

    assert config.models[_GLM].temperature == 0.9
    # A field only the old name sets still applies.
    assert config.models[_GLM].max_output_tokens == 1234


@pytest.mark.asyncio
async def test_a_pick_writes_into_the_entry_under_the_old_name(
    config_dir: Path,
) -> None:
    orchestrator = await _orchestrator(config_dir, models={_OLD: {"temperature": 0.7}})

    await _write(orchestrator, model_alias=_GLM, reasoning_effort="high")

    models = _user_models(config_dir)
    assert set(models) == {_OLD}
    assert models[_OLD]["thinking"] == "high"
    assert models[_OLD]["temperature"] == 0.7
    assert orchestrator.config.models[_GLM].thinking == "high"
    # During the rollout the pin names the model the way the file does, so an
    # older Vibe without the catalog still resolves it.
    assert _user_active_model(config_dir) == _OLD


@pytest.mark.asyncio
async def test_a_pick_writes_into_the_catalog_id_entry_when_there_is_one(
    config_dir: Path,
) -> None:
    orchestrator = await _orchestrator(
        config_dir, models={_OLD: {"temperature": 0.7}, _GLM: {"temperature": 0.9}}
    )

    await _write(orchestrator, model_alias=_GLM, reasoning_effort="high")

    models = _user_models(config_dir)
    assert "thinking" not in models[_OLD]
    assert models[_GLM]["thinking"] == "high"
    assert orchestrator.config.models[_GLM].thinking == "high"


@pytest.mark.asyncio
async def test_a_pick_into_one_of_two_old_names_is_the_one_that_applies(
    config_dir: Path,
) -> None:
    orchestrator = await _orchestrator(
        config_dir,
        aliases=(_OLD, "glm-old"),
        models={_OLD: {"temperature": 0.7}, "glm-old": {"temperature": 0.5}},
    )
    # The alias that sorts first wins: glm-5-3.
    assert orchestrator.config.models[_GLM].temperature == 0.7

    await _write(orchestrator, model_alias=_GLM, reasoning_effort="high")

    models = _user_models(config_dir)
    assert models[_OLD]["thinking"] == "high"
    assert "thinking" not in models["glm-old"]
    assert orchestrator.config.models[_GLM].thinking == "high"


@pytest.mark.asyncio
async def test_a_utility_model_under_the_old_name_uses_the_catalog_model(
    config_dir: Path,
) -> None:
    config = (await _orchestrator(config_dir, utility_models={"title": _OLD})).config

    model = config.get_utility_model(UtilityFeature.TITLE)
    assert model is not None
    assert model.alias == _GLM
    assert config.validation_warnings == ()


@pytest.mark.asyncio
async def test_a_full_model_under_the_old_name_keeps_the_name(config_dir: Path) -> None:
    config = (
        await _orchestrator(
            config_dir,
            models=[{"name": "local-glm", "provider": "llamacpp", "alias": _OLD}],
        )
    ).config

    assert config.models[_OLD].provider == "llamacpp"
    assert config.models[_GLM].provider == "mistral"
    assert config.resolve_model_name(_OLD) == _OLD


@pytest.mark.asyncio
async def test_settings_under_an_alias_two_models_claim_stay_orphaned(
    config_dir: Path,
) -> None:
    orchestrator = await build_default_orchestrator()
    parsed = parse_model_catalog_response({
        "models": [
            {
                "id": model_id,
                "label": model_id,
                "aliases": ["shared"],
                "capabilities": {"function_calling": True},
            }
            for model_id in (_GLM, "mistral-medium-latest")
        ]
    })
    assert parsed is not None
    await apply_model_catalog(orchestrator, parsed)
    _write_user_config(config_dir, models={"shared": {"temperature": 0.7}})
    await orchestrator.reload()

    config = orchestrator.config
    assert config.models[_GLM].temperature != 0.7
    assert config.validation_warnings == (
        "Settings for model 'shared' are ignored: no such model is available.",
    )


@pytest.mark.asyncio
async def test_without_the_catalog_the_old_name_stays_a_plain_key(
    config_dir: Path,
) -> None:
    config = (
        await _orchestrator(
            config_dir, with_catalog=False, models={_OLD: {"temperature": 0.7}}
        )
    ).config

    assert _GLM not in config.models
    assert config.resolve_model_name(_OLD) == _OLD
    assert config.validation_warnings == (
        f"Settings for model '{_OLD}' are ignored: no such model is available.",
    )


@pytest.mark.asyncio
async def test_the_settings_screen_edits_the_threshold_set_under_the_old_name(
    config_dir: Path,
) -> None:
    orchestrator = await _orchestrator(
        config_dir, active_model=_GLM, models={_OLD: {"auto_compact_threshold": 50_000}}
    )
    values = await collect_layer_values(orchestrator.layers, orchestrator.config)

    threshold = next(
        wire
        for wire in build_field_wires(orchestrator.config, values)
        if wire.name == "auto_compact_threshold"
    )

    assert threshold.value == 50_000
    assert threshold.origin == UserConfigLayer.NAME
    assert threshold.path == f"/models/{_GLM}/auto_compact_threshold"


@pytest.mark.asyncio
async def test_a_profile_level_under_the_old_name_is_the_profile_s(
    config_dir: Path,
) -> None:
    orchestrator = await _orchestrator(config_dir, active_model=_GLM)

    apply_profile_overrides(orchestrator, {"models": {_OLD: {"thinking": "high"}}})

    assert orchestrator.config.models[_GLM].thinking == "high"
    assert not model_thinking_is_session_writable(orchestrator, _GLM)


@pytest.mark.asyncio
async def test_an_alias_map_set_in_the_user_file_reads_and_writes_the_same(
    config_dir: Path,
) -> None:
    """A hand-set ``catalog_model_aliases`` in the user file is the topmost
    layer, so it wins the replace-merge like the merged field: the read side
    and the write side must agree on it, or a write lands in an orphaned entry.
    """
    orchestrator = await _orchestrator(
        config_dir,
        catalog_model_aliases={_GLM: [_OLD, "user-alias"]},
        models={"user-alias": {"temperature": 0.7}},
    )
    config = orchestrator.config

    assert config.resolve_model_name("user-alias") == _GLM
    assert config.models[_GLM].temperature == 0.7
    assert config.validation_warnings == ()

    await _write(orchestrator, model_alias=_GLM, reasoning_effort="high")

    models = _user_models(config_dir)
    assert models["user-alias"]["thinking"] == "high"


@pytest.mark.asyncio
async def test_a_routed_model_claiming_the_old_name_keeps_the_entry_orphaned(
    config_dir: Path,
) -> None:
    """A routed model injected from a layer that does not materialize it under
    ``models`` (here the user file) is still a model key: an alias it claims
    resolves to nothing, and the read side and the write side must agree.
    """
    orchestrator = await _orchestrator(
        config_dir,
        models={_OLD: {"temperature": 0.7}},
        routed_extra_models=[
            {"name": "routed-glm", "provider": "mistral", "alias": _OLD}
        ],
    )

    config = orchestrator.config
    # The alias is a model key, so it resolves to nothing and the entry under
    # it stays orphaned: it must not move onto the catalog model, and the read
    # side (prepare_layers) and the write side (resolve_model_name) agree.
    assert config.models[_GLM].temperature != 0.7
    assert config.resolve_model_name(_OLD) == _OLD
    assert config.validation_warnings == (
        f"Settings for model '{_OLD}' are ignored: no such model is available.",
    )


@pytest.mark.asyncio
async def test_a_pick_without_an_entry_in_the_file_pins_the_catalog_id(
    config_dir: Path,
) -> None:
    orchestrator = await _orchestrator(config_dir)

    await _write(orchestrator, model_alias=_GLM, reasoning_effort="high")

    assert _user_active_model(config_dir) == _GLM
    models = _user_models(config_dir)
    assert models[_GLM]["thinking"] == "high"


@pytest.mark.asyncio
async def test_a_pick_pins_the_catalog_id_when_the_file_has_both_entries(
    config_dir: Path,
) -> None:
    orchestrator = await _orchestrator(
        config_dir, models={_OLD: {"temperature": 0.7}, _GLM: {"temperature": 0.9}}
    )

    await _write(orchestrator, model_alias=_GLM, reasoning_effort="high")

    assert _user_active_model(config_dir) == _GLM


@pytest.mark.asyncio
async def test_a_whole_entry_upsert_under_the_old_name_keeps_a_matching_alias(
    config_dir: Path,
) -> None:
    """A whole-entry write whose path is rewritten to the layer's old-name key
    must also carry that key as its alias, or the patched layer fails to
    normalize (key/alias mismatch).
    """
    from vibe.core.config.patch import AddOperationPatch

    orchestrator = await _orchestrator(config_dir, models={_OLD: {"temperature": 0.7}})
    failures = await orchestrator.apply_patch(
        [
            AddOperationPatch(
                path=f"/models/{_GLM}",
                value={
                    "name": "glm-renamed",
                    "provider": "mistral",
                    "alias": _GLM,
                    "temperature": 0.4,
                },
                target_layer_name=UserConfigLayer.NAME,
            )
        ],
        reason="test upsert",
    )

    assert failures == []
    models = _user_models(config_dir)
    assert set(models) == {_OLD}
    assert models[_OLD]["alias"] == _OLD
    assert models[_OLD]["temperature"] == 0.4


@pytest.mark.asyncio
async def test_a_malformed_alias_map_is_a_schema_error_not_a_typeerror(
    config_dir: Path,
) -> None:
    """A hand-edited ``catalog_model_aliases`` with a non-list value must fail
    schema validation, not raise a raw TypeError while iterating the map.
    """
    from vibe.core.config.builder import InvalidConfigError

    with pytest.raises(InvalidConfigError):
        await _orchestrator(config_dir, catalog_model_aliases={_GLM: 1})  # type: ignore[dict-item]


@pytest.mark.asyncio
async def test_a_remove_lands_where_the_field_is_not_where_a_set_would_go(
    config_dir: Path,
) -> None:
    """Both entries exist for one model: a remove of a field must be routed to
    the entry that actually holds it, not to the first write-target key.
    """
    from vibe.core.config.patch import RemoveOperationPatch

    orchestrator = await _orchestrator(
        config_dir, models={_OLD: {"temperature": 0.7}, _GLM: {"max_output_tokens": 99}}
    )

    failures = await orchestrator.apply_patch(
        [
            RemoveOperationPatch(
                path=f"/models/{_GLM}/temperature",
                target_layer_name=UserConfigLayer.NAME,
            )
        ],
        reason="test remove",
    )

    assert failures == []
    models = _user_models(config_dir)
    assert "temperature" not in models[_OLD]
    assert models[_GLM]["max_output_tokens"] == 99


@pytest.mark.asyncio
async def test_a_remove_clears_the_field_on_every_entry_of_the_model(
    config_dir: Path,
) -> None:
    """Both the catalog-ID and the alias entry set the same field: a reset must
    clear both, or the leftover alias value still applies after the merge.
    """
    from vibe.core.config.patch import RemoveOperationPatch

    orchestrator = await _orchestrator(
        config_dir, models={_OLD: {"temperature": 0.7}, _GLM: {"temperature": 0.9}}
    )
    assert orchestrator.config.models[_GLM].temperature == 0.9

    failures = await orchestrator.apply_patch(
        [
            RemoveOperationPatch(
                path=f"/models/{_GLM}/temperature",
                target_layer_name=UserConfigLayer.NAME,
            )
        ],
        reason="test reset",
    )

    assert failures == []
    models = _user_models(config_dir)
    assert "temperature" not in models[_OLD]
    assert "temperature" not in models[_GLM]
    assert orchestrator.config.models[_GLM].temperature != 0.7
    assert orchestrator.config.models[_GLM].temperature != 0.9
