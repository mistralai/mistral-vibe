from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from tests.stubs.experiment_responses import behavior_eval_response
from vibe.core.config import build_default_orchestrator
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.vibe_schema import DEFAULT_ACTIVE_MODEL_CONFIG
from vibe.core.experiments.manager import config_variants_from_response
from vibe.core.experiments.models import EvalResponse
import vibe.core.experiments.wait as wait_module
from vibe.core.experiments.wait import experiments_pending, experiments_wait


async def _config(**fields: Any) -> Any:
    return await build_default_orchestrator({
        "enable_telemetry": True,
        "experiments": {"enable": True},
        "active_model": "",
        **fields,
    })


@pytest.mark.asyncio
async def test_an_unpinned_session_without_a_cached_eval_waits() -> None:
    assert experiments_pending(await _config()) is True


@pytest.mark.asyncio
async def test_a_set_model_still_waits() -> None:
    # A pick of a model the config does not offer yet waits for the eval too;
    # a first turn on a set model checks that itself.
    orchestrator = await _config(active_model=DEFAULT_ACTIVE_MODEL_CONFIG.alias)

    assert experiments_pending(orchestrator) is True


@pytest.mark.asyncio
async def test_a_cached_eval_means_no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wait_module,
        "load_cached_eval_response",
        lambda _config, _credentials=None: EvalResponse.model_validate({
            "features": {}
        }),
    )

    assert experiments_pending(await _config()) is False


@pytest.mark.asyncio
async def test_a_cached_eval_the_session_loaded_means_no_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = behavior_eval_response("Experiment behavior.")
    monkeypatch.setattr(
        wait_module, "load_cached_eval_response", lambda _c, _credentials=None: cached
    )
    orchestrator = await _config()
    layer = orchestrator.get_layer(GrowthbookLayer.NAME)
    assert isinstance(layer, GrowthbookLayer)
    layer.set_variants(config_variants_from_response(cached))

    assert experiments_pending(orchestrator) is False


@pytest.mark.asyncio
async def test_a_cached_eval_written_after_the_config_was_built_still_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Another session's eval landed between this config's build and its open.
    orchestrator = await _config()
    cached = behavior_eval_response("Experiment behavior.")
    monkeypatch.setattr(
        wait_module, "load_cached_eval_response", lambda _c, _credentials=None: cached
    )

    assert experiments_pending(orchestrator) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [{"enable_telemetry": False}, {"experiments": {"enable": False}}],
    ids=["telemetry-off", "experiments-off"],
)
async def test_no_eval_means_no_wait(fields: dict[str, Any]) -> None:
    assert experiments_pending(await _config(**fields)) is False


@pytest.mark.asyncio
async def test_no_mistral_credential_means_no_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wait_module, "mistral_credentials", lambda _config, _credentials=None: None
    )

    assert experiments_pending(await _config()) is False


@pytest.mark.asyncio
async def test_an_eval_slower_than_the_cap_ends_the_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wait_module, "EVAL_WAIT_CAP_SECONDS", 0.01)
    task = asyncio.create_task(asyncio.sleep(3600))
    wait = experiments_wait(task)

    await wait.wait()

    assert not task.done()
    assert wait.done is True
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
