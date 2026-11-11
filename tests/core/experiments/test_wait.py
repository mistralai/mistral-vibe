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
from vibe.core.experiments.wait import ExperimentsWait, experiments_wait_needed
from vibe.core.utils.fetch_wait import FetchFailures


async def _config(**fields: Any) -> Any:
    return await build_default_orchestrator({
        "enable_telemetry": True,
        "experiments": {"enable": True},
        "active_model": "",
        **fields,
    })


@pytest.mark.asyncio
async def test_an_unpinned_session_without_a_cached_eval_waits() -> None:
    assert experiments_wait_needed(await _config(), FetchFailures()) is True


@pytest.mark.asyncio
async def test_a_set_model_does_not_wait() -> None:
    orchestrator = await _config(active_model=DEFAULT_ACTIVE_MODEL_CONFIG.alias)

    assert experiments_wait_needed(orchestrator, FetchFailures()) is False


@pytest.mark.asyncio
async def test_a_cached_eval_means_no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wait_module,
        "load_cached_eval_response",
        lambda _config, _credentials=None: EvalResponse.model_validate({
            "features": {}
        }),
    )

    assert experiments_wait_needed(await _config(), FetchFailures()) is False


@pytest.mark.asyncio
async def test_a_cached_eval_the_session_loaded_means_no_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = routing_eval_response("experiment-routed")
    monkeypatch.setattr(
        wait_module, "load_cached_eval_response", lambda _c, _credentials=None: cached
    )
    orchestrator = await _config()
    layer = orchestrator.get_layer(GrowthbookLayer.NAME)
    assert isinstance(layer, GrowthbookLayer)
    layer.set_variants(config_variants_from_response(cached))

    assert experiments_wait_needed(orchestrator, FetchFailures()) is False


@pytest.mark.asyncio
async def test_a_cached_eval_written_after_the_config_was_built_still_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Another session's eval landed between this config's build and its open.
    orchestrator = await _config()
    cached = routing_eval_response("experiment-routed")
    monkeypatch.setattr(
        wait_module, "load_cached_eval_response", lambda _c, _credentials=None: cached
    )

    assert experiments_wait_needed(orchestrator, FetchFailures()) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [{"enable_telemetry": False}, {"experiments": {"enable": False}}],
    ids=["telemetry-off", "experiments-off"],
)
async def test_no_eval_means_no_wait(fields: dict[str, Any]) -> None:
    assert experiments_wait_needed(await _config(**fields), FetchFailures()) is False


@pytest.mark.asyncio
async def test_no_mistral_credential_means_no_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wait_module, "mistral_credentials", lambda _config, _credentials=None: None
    )

    assert experiments_wait_needed(await _config(), FetchFailures()) is False


@pytest.mark.asyncio
async def test_no_wait_once_an_eval_failed_in_this_process() -> None:
    failures = FetchFailures()
    failures.mark()

    assert experiments_wait_needed(await _config(), failures) is False


@pytest.mark.asyncio
async def test_an_eval_slower_than_the_cap_fails_the_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wait_module, "EVAL_WAIT_CAP_SECONDS", 0.01)
    task = asyncio.create_task(asyncio.sleep(3600))
    failures = FetchFailures()
    wait = ExperimentsWait(task, failures)

    await wait.wait()

    assert not task.done()
    assert failures.failed is True
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
