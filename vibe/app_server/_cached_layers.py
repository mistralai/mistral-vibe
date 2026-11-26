"""The config layers a build fills from what earlier fetches cached."""

from __future__ import annotations

from vibe.app_server._admin_config import apply_held_admin_config
from vibe.app_server._model_catalog_service import ModelCatalogKey, ModelCatalogService
from vibe.app_server._org_config_service import OrgConfigKey, OrgConfigService
from vibe.core.config import VibeConfigSchema
from vibe.core.config.admin_config import AdminConfigApplyResult, AdminConfigOutcome
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.model_catalog import apply_model_catalog
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.experiments.cache import load_cached_eval_response
from vibe.core.experiments.manager import config_variants_from_response
from vibe.core.platform_auth import PlatformCredentialFactory
from vibe.core.platform_auth._mistral import mistral_credentials
from vibe.observability.logging import logger
from vibe.observability.sentry import capture_cli_exception

OIDC_TENANT_UNRESOLVED = AdminConfigApplyResult(
    AdminConfigOutcome.FETCH_FAILED, error="OIDC tenant not resolved"
)


def oidc_tenant_unresolved(
    config: VibeConfigSchema, credentials: PlatformCredentialFactory | None = None
) -> bool:
    """Signed in with a user token whose tenant, which keys the org config, is
    not known yet: the org config is unknown, not absent. An API key always
    names its principal, so it never reads as unresolved.
    """
    mistral = mistral_credentials(config, credentials)
    return mistral is not None and mistral.cached_principal() is None


async def org_config_key(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    credentials: PlatformCredentialFactory | None = None,
) -> OrgConfigKey | None:
    """The org config's key, resolving the credential's principal first.

    A user token has no cache identity until its tenant is known.
    """
    from vibe.core.telemetry.send import resolve_credential_cache_key

    await resolve_credential_cache_key(orchestrator.config, credentials)
    return OrgConfigService.key_for(orchestrator, credentials)


async def apply_cached_layers(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    org_config: OrgConfigService | None,
    credentials: PlatformCredentialFactory | None = None,
    *,
    model_catalog: ModelCatalogService | None,
) -> tuple[OrgConfigKey | None, str | None, AdminConfigApplyResult | None]:
    """Apply the held org config, the cached experiment variants and the held
    model catalog.

    Session builds and host reads (``agents/list``, ``config/read``) both go
    through here, so they see the same config. ``org_config=None`` skips the
    org config, and ``model_catalog=None`` the catalog, for a build on local
    config only.

    Returns the org config's key, read before the org config applies since it
    can change the provider the key is made of; the org config it tried, if
    any; and why it did not apply.
    """
    org_key = await org_config_key(orchestrator, credentials)
    tried, failure = (
        await apply_held_admin_config(orchestrator, await org_config.load(org_key))
        if org_config is not None
        else (None, None)
    )
    await _apply_cached_experiment_variants(orchestrator, credentials)
    if model_catalog is not None:
        await apply_held_model_catalog(orchestrator, model_catalog, credentials)
    return org_key, tried, failure


async def apply_held_model_catalog(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    model_catalog: ModelCatalogService,
    credentials: PlatformCredentialFactory | None = None,
) -> None:
    """Apply the catalog the process holds for the config's account, without
    fetching. Fail-open: a catalog the config cannot be built from is ignored.
    """
    key = ModelCatalogKey.current(orchestrator.config, credentials)
    catalog = await model_catalog.load(key)
    if catalog is None:
        return
    try:
        await apply_model_catalog(orchestrator, catalog)
    except Exception as exc:
        logger.exception("Failed to apply the held model catalog to config")
        capture_cli_exception(exc, tags={"vibe_boundary": "apply_held_model_catalog"})


async def _apply_cached_experiment_variants(
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema],
    credentials: PlatformCredentialFactory | None = None,
) -> None:
    """Apply the last cached experiment variants to config before first render."""
    cached = load_cached_eval_response(config_orchestrator.config, credentials)
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
