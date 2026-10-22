"""The config layers a build fills from what earlier fetches cached."""

from __future__ import annotations

from vibe.app_server._admin_config import apply_cached_admin_config
from vibe.core.config import VibeConfigSchema
from vibe.core.config.admin_config import AdminConfigApplyResult
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.model_catalog import apply_cached_model_catalog
from vibe.core.config.managed_config_cache import managed_config_cache_key
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.experiments.cache import load_cached_eval_response
from vibe.core.experiments.manager import config_variants_from_response


async def apply_cached_layers(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], *, org_config: bool = True
) -> tuple[str | None, str | None, AdminConfigApplyResult | None]:
    """Apply the cached org config, experiment variants and model catalog.

    Session builds and host reads (``agents/list``, ``config/read``) both go
    through here, so they see the same config. ``org_config=False`` skips the
    org config, for a build on local config only.

    Returns the managed-config cache key, computed before the org config
    applies since it can change the Mistral provider the key derives from;
    the cached org config that applied, if any; and why it did not apply.
    """
    cache_key = managed_config_cache_key(orchestrator.config)
    saved, saved_failure = (
        await apply_cached_admin_config(orchestrator, cache_key)
        if org_config
        else (None, None)
    )
    await _apply_cached_experiment_variants(orchestrator)
    await apply_cached_model_catalog(orchestrator)
    return cache_key, saved, saved_failure


async def _apply_cached_experiment_variants(
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> None:
    """Apply the last cached experiment variants to config before first render."""
    cached = load_cached_eval_response(config_orchestrator.config)
    if cached is None:
        return
    variants = config_variants_from_response(cached)
    if not variants:
        return
    try:
        layer = config_orchestrator.get_layer(GrowthbookLayer.NAME)
    except KeyError:
        return
    if isinstance(layer, GrowthbookLayer):
        layer.set_variants(variants)
        await config_orchestrator.reload()
