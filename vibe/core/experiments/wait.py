"""Holding a session's first turn for the GrowthBook eval.

With no cached eval result, a session whose model is not set may be about to
get it from an experiment: its first turn waits for the eval, for at most
``EVAL_WAIT_CAP_SECONDS`` from when the session opened. A pick of a model the
config does not offer yet waits for it too, whatever the session's model.

Once an eval fails, or is still running when a cap expires, no later session
in the process waits: the process's ``ExperimentsService`` holds that memory.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from vibe.core.config._self_hosted import experiments_allowed
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.experiments._constants import EVAL_WAIT_CAP_SECONDS
from vibe.core.experiments.cache import load_cached_eval_response
from vibe.core.experiments.manager import config_variants_from_response
from vibe.core.platform_auth._mistral import mistral_credentials
from vibe.core.utils.fetch_wait import FetchFailures, FetchWait

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.config.orchestrator import ConfigOrchestrator
    from vibe.core.platform_auth import PlatformCredentialFactory


def experiments_eval_runs(
    config: VibeConfigSchema, credentials: PlatformCredentialFactory | None = None
) -> bool:
    """Whether a session on *config* sends an eval: telemetry, the A/B opt-in,
    a deployment that is not self-hosted and a Mistral credential are all
    needed.
    """
    if not experiments_allowed(config):
        return False
    return mistral_credentials(config, credentials) is not None


def experiments_pending(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    failures: FetchFailures,
    credentials: PlatformCredentialFactory | None = None,
) -> bool:
    """Whether the variants of a session opening on *orchestrator* may still
    change: an eval runs, none failed in this process, and none is held.

    Only a session left on the default model, or a pick of a model the config
    does not offer yet, has something to wait for: each waiter checks that.
    """
    if not experiments_eval_runs(orchestrator.config, credentials):
        return False
    if failures.failed:
        return False
    return not _loaded_the_cached_eval(orchestrator, credentials)


def _loaded_the_cached_eval(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    credentials: PlatformCredentialFactory | None = None,
) -> bool:
    cached = load_cached_eval_response(orchestrator.config, credentials)
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
    def __init__(self, task: asyncio.Task[Any], failures: FetchFailures) -> None:
        super().__init__(
            task,
            name="GrowthBook eval",
            cap_seconds=EVAL_WAIT_CAP_SECONDS,
            failures=failures,
        )
