"""A saved thinking pick equal to the model's default level is not stored.

The default level is the one the model runs on when no one chose one: the
GrowthBook catalog filter's ``settings.thinking`` when it sets one, else the
catalog's default. A stored level would keep the model off later changes of
that default.

The catalog lists Mistral Large 4 (``mistral-large-4-0``, also called
``mistral-large-4-0-3``) with low, medium and high, so its catalog default is
high.
"""

from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any, cast

import pytest
import tomli_w

from tests.app_server.test_unified_harness_backend_adapter import _RecordingSession
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.protocol import SessionOptions

_LARGE = "mistral-large-4-0"
_OLD = "mistral-large-4-0-3"


def _write_user_config(config_dir: Path, **fields: Any) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    # The base test config pins a model the catalog replaces.
    config["active_model"] = ""
    config.update(fields)
    # No model entry is written as no `models` key, as a user's file has.
    if config.get("models") == []:
        del config["models"]
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


def _user_config(config_dir: Path) -> dict[str, Any]:
    return tomllib.loads((config_dir / "config.toml").read_text(encoding="utf-8"))


def _user_models(config_dir: Path) -> dict[str, dict[str, Any]]:
    models = _user_config(config_dir).get("models", [])
    return {entry["alias"]: entry for entry in models}


async def _adapter(
    tmp_path: Path,
    config_dir: Path,
    process_services: ProcessServices,
    *,
    catalog_filter: object | None = None,
    **user_fields: Any,
) -> Any:
    """A session on Mistral Large 4 from the catalog, under *catalog_filter*,
    with *user_fields* in the user's config file.
    """
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.core.config.layers.growthbook import GrowthbookLayer
    from vibe.core.config.layers.model_catalog import apply_model_catalog
    from vibe.core.experiments.active import ExperimentName
    from vibe.core.model_catalog import parse_model_catalog_response

    _write_user_config(config_dir, **user_fields)
    process = HarnessProcess(services=process_services)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    orchestrator = context.config_orchestrator
    if catalog_filter is not None:
        growthbook = orchestrator.get_layer(GrowthbookLayer.NAME)
        assert isinstance(growthbook, GrowthbookLayer)
        growthbook.set_variants({
            ExperimentName.MODEL_CATALOG_FILTER.value: catalog_filter
        })
    catalog = parse_model_catalog_response({
        "models": [
            {
                "id": _LARGE,
                "label": "Mistral Large 4",
                "recommended": True,
                "aliases": [_OLD],
                "parameters": {"reasoning": ["low", "medium", "high"]},
                "capabilities": {"function_calling": True},
            }
        ]
    })
    assert catalog is not None
    await apply_model_catalog(orchestrator, catalog)
    await orchestrator.reload()
    return UnifiedHarnessBackendAdapter(
        cast(Any, _RecordingSession()),
        context,
        context.derive(UnifiedSessionSettings()),
    )


async def _pick(adapter: Any, level: str, *, model_alias: str | None = None) -> None:
    from vibe.app_server.protocol import ModelConfigWriteParams

    result = await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id,
            model_alias=model_alias,
            reasoning_effort=level,
        )
    )
    assert result.response.failures == []
    assert result.response.rejected is False


_MEDIUM_FILTER = {"settings": {_LARGE: {"thinking": "medium"}}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("catalog_filter", "default"),
    [(None, "high"), (_MEDIUM_FILTER, "medium")],
    ids=["catalog-default", "filter-default"],
)
@pytest.mark.parametrize("names_model", [False, True], ids=["thinking", "model"])
async def test_picking_the_default_level_saves_no_thinking(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    catalog_filter: object | None,
    default: str,
    names_model: bool,
) -> None:
    """*Prepare*: A user file with no entry for Mistral Large 4, with and
    without a filter setting its level.
    *Do*: Pick its default level, alone or with the model.
    *Assert*: The file holds no thinking for it, and the session runs the
    default: the filter's when it sets one, else the catalog's.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    adapter = await _adapter(
        tmp_path, config_dir, process_services, catalog_filter=catalog_filter
    )

    await _pick(adapter, default, model_alias=_LARGE if names_model else None)

    assert _LARGE not in _user_models(config_dir)
    model = adapter._context.config_orchestrator.config.models[_LARGE]
    assert model.thinking == default


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("catalog_filter", "picked"),
    [(None, "medium"), (_MEDIUM_FILTER, "high")],
    ids=["catalog-default", "filter-default"],
)
async def test_picking_another_level_saves_it(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    catalog_filter: object | None,
    picked: str,
) -> None:
    """*Prepare*: Mistral Large 4, with and without a filter setting its level.
    *Do*: Pick a level other than its default (the catalog's high is not the
    default under a filter setting medium).
    *Assert*: The level is saved, as before.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    adapter = await _adapter(
        tmp_path, config_dir, process_services, catalog_filter=catalog_filter
    )

    await _pick(adapter, picked)

    assert _user_models(config_dir)[_LARGE]["thinking"] == picked


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [_LARGE, _OLD], ids=["id", "old-name"])
async def test_picking_the_default_over_a_saved_level_removes_the_entry(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    key: str,
) -> None:
    """*Prepare*: A user file saving high for Mistral Large 4, under its id or
    its old name, and nothing else for it; the filter sets medium.
    *Do*: Pick medium.
    *Assert*: The entry is gone, rather than high staying in force, and the
    session runs medium.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    adapter = await _adapter(
        tmp_path,
        config_dir,
        process_services,
        catalog_filter=_MEDIUM_FILTER,
        models=[{"alias": key, "thinking": "high"}],
    )
    assert adapter._context.config_orchestrator.config.models[_LARGE].thinking == "high"

    await _pick(adapter, "medium")

    # No entry left, and no empty `[models]` table either.
    assert "models" not in _user_config(config_dir)
    assert (
        adapter._context.config_orchestrator.config.models[_LARGE].thinking == "medium"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("loaded", "now"),
    [
        ([{"alias": _LARGE, "thinking": "high"}], []),
        ([], [{"alias": _LARGE, "thinking": "low"}]),
        ([{"alias": _LARGE, "thinking": 3}], [{"alias": _LARGE, "thinking": 3}]),
    ],
    ids=["removed-since", "saved-since", "not-a-level"],
)
async def test_picking_the_default_reads_the_file_as_it_is_now(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    loaded: list[dict[str, Any]],
    now: list[dict[str, Any]],
) -> None:
    """*Prepare*: A session loaded with one level for Mistral Large 4 in the
    user file, which another session has since changed; the filter sets medium.
    A value that is no level counts as saved too.
    *Do*: Pick medium.
    *Assert*: The pick lands and leaves no thinking in the file, whatever the
    session last loaded: removing a level already gone does not fail it, and
    one saved since, or one the config ignores, is cleared.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    adapter = await _adapter(
        tmp_path,
        config_dir,
        process_services,
        catalog_filter=_MEDIUM_FILTER,
        models=loaded,
    )
    _write_user_config(config_dir, models=now)

    await _pick(adapter, "medium")

    # No entry left, and no empty `[models]` table either.
    assert "models" not in _user_config(config_dir)
    assert (
        adapter._context.config_orchestrator.config.models[_LARGE].thinking == "medium"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["medium", "low"], ids=["default", "other"])
async def test_a_pick_reports_an_unreadable_file_as_a_failure(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    level: str,
) -> None:
    """*Prepare*: A session whose user file has since become unreadable TOML;
    the filter sets medium.
    *Do*: Pick the default level, and another one.
    *Assert*: The write answers with a failure, as any write to that file
    does, instead of raising.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import ModelConfigWriteParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    adapter = await _adapter(
        tmp_path, config_dir, process_services, catalog_filter=_MEDIUM_FILTER
    )
    (config_dir / "config.toml").write_text("this is = = not toml", encoding="utf-8")

    result = await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id, reasoning_effort=level
        )
    )

    assert result.response.failures != []


@pytest.mark.asyncio
async def test_picking_the_default_keeps_the_other_settings_of_the_entry(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A user file saving high and a temperature for Mistral Large 4
    under its old name; the filter sets medium.
    *Do*: Pick medium.
    *Assert*: Only the thinking is removed: the temperature still applies.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    adapter = await _adapter(
        tmp_path,
        config_dir,
        process_services,
        catalog_filter=_MEDIUM_FILTER,
        models=[{"alias": _OLD, "thinking": "high", "temperature": 0.7}],
    )

    await _pick(adapter, "medium")

    assert _user_models(config_dir)[_OLD] == {"alias": _OLD, "temperature": 0.7}
    model = adapter._context.config_orchestrator.config.models[_LARGE]
    assert (model.thinking, model.temperature) == ("medium", 0.7)


@pytest.mark.asyncio
async def test_a_model_only_the_user_defines_saves_every_level(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A model defined only in the user's file, with no level, so no
    layer below the file gives it a default.
    *Do*: Pick high on it.
    *Assert*: The level is saved, as before.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    local = {"name": "local-model", "provider": "mistral", "alias": "local"}
    adapter = await _adapter(
        tmp_path, config_dir, process_services, models=[local], active_model="local"
    )

    await _pick(adapter, "high")

    assert _user_models(config_dir)["local"] == {**local, "thinking": "high"}


@pytest.mark.asyncio
async def test_back_to_the_built_in_default_leaves_no_models_table(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: No catalog, so the session runs the built-in Mistral Large 4,
    whose default level is high, with no model entry in the user file.
    *Do*: Pick low, which saves it, then pick high again.
    *Assert*: The file holds no `models` at all: the emptied entry goes, and so
    does the table it leaves, instead of an empty `[models]`.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    _write_user_config(config_dir, models=[])
    context = await HarnessProcess(
        services=process_services
    ).build_unified_session_context(SessionOptions(cwd=str(tmp_path)))
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, _RecordingSession()),
        context,
        context.derive(UnifiedSessionSettings()),
    )

    await _pick(adapter, "low")
    assert _user_models(config_dir)["mistral-large-4-0"]["thinking"] == "low"
    await _pick(adapter, "high")

    assert "models" not in _user_config(config_dir)
    assert context.config_orchestrator.config.get_active_model().thinking == "high"


@pytest.mark.asyncio
async def test_picking_the_default_with_nothing_saved_leaves_the_file_untouched(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A user file with no level for Mistral Large 4, and a comment
    a rewrite would drop; the filter sets medium.
    *Do*: Pick medium.
    *Assert*: The file is not rewritten: the comment is still there.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    adapter = await _adapter(
        tmp_path, config_dir, process_services, catalog_filter=_MEDIUM_FILTER
    )
    config_file = config_dir / "config.toml"
    config_file.write_text(
        config_file.read_text(encoding="utf-8") + "# kept by hand\n", encoding="utf-8"
    )

    await _pick(adapter, "medium")

    assert "# kept by hand" in config_file.read_text(encoding="utf-8")
    assert (
        adapter._context.config_orchestrator.config.models[_LARGE].thinking == "medium"
    )


@pytest.mark.asyncio
async def test_picking_the_default_lands_when_another_session_removes_the_level_first(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A user file saving high for Mistral Large 4; the filter sets
    medium. Another session removes that level just before this pick's write
    reaches the file, after the pick read it.
    *Do*: Pick medium.
    *Assert*: The pick lands, with no failure, and leaves no level in the file:
    its removal, decided on the file it finds, does nothing.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.config.layers.user import UserConfigLayer

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    adapter = await _adapter(
        tmp_path,
        config_dir,
        process_services,
        catalog_filter=_MEDIUM_FILTER,
        models=[{"alias": _LARGE, "thinking": "high"}],
    )
    layer = adapter._context.config_orchestrator.get_layer(UserConfigLayer.NAME)
    apply = layer.apply
    raced: list[bool] = []

    async def apply_after_another_session(*args: Any, **kwargs: Any) -> None:
        if not raced:
            raced.append(True)
            _write_user_config(config_dir, models=[])
        await apply(*args, **kwargs)

    monkeypatch.setattr(layer, "apply", apply_after_another_session)

    await _pick(adapter, "medium")

    assert raced == [True]
    assert "models" not in _user_config(config_dir)
    assert (
        adapter._context.config_orchestrator.config.models[_LARGE].thinking == "medium"
    )
