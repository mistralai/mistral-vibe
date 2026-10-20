from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from tests.stubs.experiment_responses import routing_eval_response
from vibe.core.config import build_default_orchestrator
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.vibe_schema import DEFAULT_ACTIVE_MODEL_CONFIG
from vibe.core.experiments.manager import config_variants_from_response
from vibe.core.experiments.models import EvalResponse
import vibe.core.experiments.wait as wait_module
from vibe.core.experiments.wait import (
    ExperimentsWait,
    experiments_eval_failed,
    experiments_wait_needed,
    mark_experiments_eval_failed,
)
from vibe.core.model_catalog.wait import mark_model_catalog_failed, model_catalog_failed


async def _config(**fields: Any) -> Any:
    return await build_default_orchestrator({
        "enable_telemetry": True,
        "experiments": {"enable": True},
        "active_model": "",
        **fields,
    })


@pytest.mark.asyncio
async def test_an_unpinned_session_without_a_cached_eval_waits() -> None:
    assert experiments_wait_needed(await _config()) is True


@pytest.mark.asyncio
async def test_a_set_model_does_not_wait() -> None:
    orchestrator = await _config(active_model=DEFAULT_ACTIVE_MODEL_CONFIG.alias)

    assert experiments_wait_needed(orchestrator) is False


@pytest.mark.asyncio
async def test_a_cached_eval_means_no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wait_module,
        "load_cached_eval_response",
        lambda _config: EvalResponse.model_validate({"features": {}}),
    )

    assert experiments_wait_needed(await _config()) is False


@pytest.mark.asyncio
async def test_a_cached_eval_the_session_loaded_means_no_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = routing_eval_response("experiment-routed")
    monkeypatch.setattr(wait_module, "load_cached_eval_response", lambda _c: cached)
    orchestrator = await _config()
    layer = orchestrator.get_layer(GrowthbookLayer.NAME)
    assert isinstance(layer, GrowthbookLayer)
    layer.set_variants(config_variants_from_response(cached))

    assert experiments_wait_needed(orchestrator) is False


@pytest.mark.asyncio
async def test_a_cached_eval_written_after_the_config_was_built_still_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Another session's eval landed between this config's build and its open.
    orchestrator = await _config()
    cached = routing_eval_response("experiment-routed")
    monkeypatch.setattr(wait_module, "load_cached_eval_response", lambda _c: cached)

    assert experiments_wait_needed(orchestrator) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [{"enable_telemetry": False}, {"experiments": {"enable": False}}],
    ids=["telemetry-off", "experiments-off"],
)
async def test_no_eval_means_no_wait(fields: dict[str, Any]) -> None:
    assert experiments_wait_needed(await _config(**fields)) is False


@pytest.mark.asyncio
async def test_no_mistral_credential_means_no_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wait_module, "get_mistral_provider_and_api_key", lambda _config: None
    )

    assert experiments_wait_needed(await _config()) is False


@pytest.mark.asyncio
async def test_no_wait_once_an_eval_failed_in_this_process() -> None:
    mark_experiments_eval_failed()

    assert experiments_wait_needed(await _config()) is False


@pytest.mark.asyncio
async def test_a_failed_catalog_fetch_does_not_stop_the_eval_wait() -> None:
    mark_model_catalog_failed()

    assert experiments_wait_needed(await _config()) is True


@pytest.mark.asyncio
async def test_an_eval_slower_than_the_cap_fails_only_the_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wait_module, "EVAL_WAIT_CAP_SECONDS", 0.01)
    task = asyncio.create_task(asyncio.sleep(3600))
    wait = ExperimentsWait(task)

    await wait.wait()

    assert not task.done()
    assert experiments_eval_failed() is True
    assert model_catalog_failed() is False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
