"""The auth OnboardingContext served from the config this process holds.

ACP auth surfaces must resolve the active provider the way the sessions in
the same process do — over the held org config, the cached growthbook and
model-catalog layers (``apply_cached_layers``) and the process's credential
selection, which the org config and caches are keyed by — or a status read
can report a provider the running sessions do not use.
``SessionConfigCache`` rebuilds only when the files it reads change, so
status polls do not run config migrations or TLS reconfiguration per call.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from vibe.app_server._cached_layers import apply_cached_layers
from vibe.app_server._model_catalog_service import ModelCatalogService
from vibe.app_server._org_config_service import OrgConfigService
from vibe.app_server._session_config_cache import (
    BuiltSessionConfig,
    SessionConfigCache,
    SessionConfigKey,
)
from vibe.core.config import VibeConfigSchema
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
)
from vibe.core.platform_auth import PlatformCredentialFactory
from vibe.core.platform_auth._mistral import api_key_credentials
from vibe.observability.logging import logger
from vibe.setup.auth.onboarding_context import (
    CONFIG_ERROR_MODEL_INVALID,
    OnboardingContext,
)


class OnboardingContextReader:
    """Loads the ACP auth context from a config built like a session's."""

    def __init__(
        self,
        *,
        org_config: OrgConfigService | None,
        model_catalog: ModelCatalogService | None = None,
        harness_files: HarnessFilesManager | None = None,
        credentials: PlatformCredentialFactory | None = None,
    ) -> None:
        self._org_config = org_config
        self._model_catalog = model_catalog
        self._harness_files = harness_files or get_harness_files_manager()
        self._credentials = credentials or api_key_credentials
        self._cwd = self._harness_files.cwd or Path.cwd()
        self._configs = SessionConfigCache()
        self._key = SessionConfigKey.of(
            self._cwd, {}, org_config=org_config is not None
        )

    async def load(self) -> OnboardingContext:
        files = self._harness_files.for_session(self._cwd)

        async def build() -> BuiltSessionConfig:
            orchestrator = await build_default_orchestrator(harness_files=files)
            pre_org_config = orchestrator.config
            key, tried, failure = await apply_cached_layers(
                orchestrator,
                self._org_config,
                self._credentials,
                model_catalog=self._model_catalog,
            )
            return BuiltSessionConfig(
                orchestrator,
                pre_org_config=pre_org_config,
                org_config_key=key,
                org_config_tried=tried,
                org_config_failure=failure,
            )

        try:
            config = await self._configs.config(
                self._key, files, self._org_config, build, self._credentials
            )
        except Exception:
            # A config that cannot be built must not block ACP auth: status,
            # sign-in and sign-out still answer over the default provider,
            # with a reason protocol surfaces report.
            logger.warning(
                "Auth config could not be read; using the default config.",
                exc_info=True,
            )
            return replace(
                OnboardingContext.from_config(VibeConfigSchema.model_construct()),
                config_error=CONFIG_ERROR_MODEL_INVALID,
            )
        return OnboardingContext.from_config(config)


__all__ = ["OnboardingContextReader"]
