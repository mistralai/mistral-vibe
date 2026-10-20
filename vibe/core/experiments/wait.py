"""Holding a session's first turn for the GrowthBook eval.

With no cached eval result, a session whose model is not set may be about to
get it from an experiment: its first turn waits for the eval, for at most
``EVAL_WAIT_CAP_SECONDS`` from when the session opened. Once an eval fails, or
is still running when a cap expires, no later session in the process waits.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.experiments._constants import EVAL_WAIT_CAP_SECONDS
from vibe.core.experiments.cache import load_cached_eval_response
from vibe.core.experiments.manager import config_variants_from_response
from vibe.core.telemetry.send import get_mistral_provider_and_api_key
from vibe.core.utils.fetch_wait import FetchFailures, FetchWait

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.config.orchestrator import ConfigOrchestrator

_failures = FetchFailures()


def experiments_eval_failed() -> bool:
    return _failures.failed


def mark_experiments_eval_failed() -> None:
    _failures.mark()


def reset_experiments_eval_failed() -> None:
    """Forget a failed eval; for test isolation."""
    _failures.reset()


def experiments_eval_runs(config: VibeConfigSchema) -> bool:
    """Whether a session on *config* sends an eval: telemetry, the A/B opt-in
    and a Mistral credential are all needed.
    """
    if not (config.enable_telemetry and config.experiments.enable):
        return False
    return get_mistral_provider_and_api_key(config) is not None


def experiments_wait_needed(orchestrator: ConfigOrchestrator[VibeConfigSchema]) -> bool:
    """Whether a session opening on *orchestrator* must hold its first turn for
    the eval.

    ``active_model`` is empty when nothing pins a model, so the session would
    run the default model, which the routing experiment may replace.
    """
    config = orchestrator.config
    if not experiments_eval_runs(config):
        return False
    if config.active_model or experiments_eval_failed():
        return False
    return not _loaded_the_cached_eval(orchestrator)


def _loaded_the_cached_eval(orchestrator: ConfigOrchestrator[VibeConfigSchema]) -> bool:
    cached = load_cached_eval_response(orchestrator.config)
    if cached is None:
        return False
    try:
        layer = orchestrator.get_layer(GrowthbookLayer.NAME)
    except KeyError:
        return True
    # Another session's eval can write the cache after this session's config
    # was built from it: only variants the session holds can route its turn.
    return not isinstance(
        layer, GrowthbookLayer
    ) or layer.variants == config_variants_from_response(cached)


class ExperimentsWait(FetchWait):
    def __init__(self, task: asyncio.Task[Any]) -> None:
        super().__init__(
            task,
            name="GrowthBook eval",
            cap_seconds=EVAL_WAIT_CAP_SECONDS,
            failures=_failures,
        )
