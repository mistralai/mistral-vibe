"""The config layers a build fills from what earlier fetches cached."""

from __future__ import annotations

from vibe.app_server._admin_config import apply_held_admin_config
from vibe.app_server._org_config_service import OrgConfigKey, OrgConfigService
from vibe.core.config import VibeConfigSchema
from vibe.core.config.admin_config import AdminConfigApplyResult, AdminConfigOutcome
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.model_catalog import apply_cached_model_catalog
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.experiments.cache import load_cached_eval_response
from vibe.core.experiments.manager import config_variants_from_response
from vibe.utils.oidc_marker import is_oidc_marker, oidc_enabled

OIDC_TENANT_UNRESOLVED = AdminConfigApplyResult(
    AdminConfigOutcome.FETCH_FAILED, error="CLI OIDC tenant not resolved"
)


def oidc_tenant_unresolved(config: VibeConfigSchema) -> bool:
    """Signed in with CLI OIDC, but the tenant that keys the org config is not
    known yet: the org config is unknown, not absent.
    """
    if not oidc_enabled():
        return False
    from vibe.core.telemetry.send import get_mistral_provider_and_api_key

    provider_and_key = get_mistral_provider_and_api_key(config)
    return (
        provider_and_key is not None
        and is_oidc_marker(provider_and_key[1])
        and OrgConfigService.key_for_config(config) is None
    )


async def org_config_key(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> OrgConfigKey | None:
    """The org config's key, discovering the CLI OIDC tenant first.

    An OIDC credential has no cache identity until its tenant is known.
    """
    from vibe.core.telemetry.send import resolve_credential_cache_key

    await resolve_credential_cache_key(orchestrator.config)
    return OrgConfigService.key_for(orchestrator)


async def apply_cached_layers(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    org_config: OrgConfigService | None,
) -> tuple[OrgConfigKey | None, str | None, AdminConfigApplyResult | None]:
    """Apply the held org config, the cached experiment variants and model
    catalog.

    Session builds and host reads (``agents/list``, ``config/read``) both go
    through here, so they see the same config. ``org_config=None`` skips the
    org config, for a build on local config only.

    Returns the org config's key, read before the org config applies since it
    can change the provider the key is made of; the org config it tried, if
    any; and why it did not apply.
    """
    org_key = await org_config_key(orchestrator)
    tried, failure = (
        await apply_held_admin_config(orchestrator, await org_config.load(org_key))
        if org_config is not None
        else (None, None)
    )
    await _apply_cached_experiment_variants(orchestrator)
    await apply_cached_model_catalog(orchestrator)
    return org_key, tried, failure


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
