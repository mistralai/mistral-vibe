"""Session-less ``setup/*`` methods for the pre-session host surface.

These serve a thin client's onboarding wizard exactly the way ``vibe/setup``
serves Python's in-process one: no session is opened, no trust gate fires,
and no runtime is built. Every write reuses the Python setup internals, so the
semantics stay identical to the in-process wizard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vibe import __version__
from vibe.app_server._dispatch import RequestFailure, method_not_found
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server.protocol import (
    ProtocolErrorCode,
    SetupProviderView,
    SetupStatusParams,
    SetupStatusResponse,
    SetupStoreCredentialParams,
    SetupStoreCredentialResponse,
    SetupSubmitChoicesParams,
    SetupSubmitChoicesResponse,
)
from vibe.core.config import (
    DEFAULT_ACTIVE_MODEL_CONFIG,
    DEFAULT_PROVIDERS,
    ProviderConfig,
    VibeConfigSchema,
    build_default_orchestrator,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.telemetry.types import LaunchContext
from vibe.observability.logging import logger
from vibe.setup.auth.api_key_persistence import (
    ProviderCredentialsPersistRequest,
    ProviderCredentialsPersistResult,
    persist_api_key,
    persist_provider_credentials,
    resolve_api_key_provider,
)
from vibe.utils.api_keys import resolve_api_key

if TYPE_CHECKING:
    from vibe.app_server._connection_protocol import ClientInfo

# Only written by the wizard path (run_onboarding does the same post-exit).
_THEME_FIELD = "/theme"


class SetupRequestHandler:
    """The pre-session wizard surface: seed snapshot, key store, choice submit."""

    def __init__(self, harness_files: HarnessFilesManager) -> None:
        self._harness_files = harness_files

    async def dispatch(
        self, method: str, raw_params: dict[str, Any], *, client_info: ClientInfo
    ) -> ProtocolModel:
        match method:
            case "setup/status":
                params = validate_wire(SetupStatusParams, raw_params)
                response = await self._status(params)
            case "setup/store-credential":
                params = validate_wire(SetupStoreCredentialParams, raw_params)
                response = await self._store_credential(params, client_info)
            case "setup/submit-choices":
                params = validate_wire(SetupSubmitChoicesParams, raw_params)
                response = await self._submit_choices(params)
            case _:
                raise method_not_found(method)
        return response

    async def _orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]:
        # The same source Python's run_cli seeds the wizard from: the fully
        # resolved schema of the default orchestrator, never raw TOML.
        return await build_default_orchestrator(harness_files=self._harness_files)

    async def _status(self, params: SetupStatusParams) -> SetupStatusResponse:
        config = (await self._orchestrator()).config
        # The failed session's handshake names the provider (the runtime's
        # own resolution path), so the wizard seeds for the provider that
        # actually lacks a key — never the active provider, which can differ.
        if params.provider is None:
            provider = _seed_provider(config)
        else:
            provider = _named_provider(config, params.provider)
        env_var = provider.api_key_env_var
        return SetupStatusResponse(
            provider=_provider_view(provider),
            console_base_url=config.console_base_url,
            vibe_base_url=config.vibe_base_url,
            active_model=_active_model(config),
            theme=config.theme,
            supports_browser_sign_in=provider.supports_browser_sign_in,
            # An empty env var names no secret to resolve, exactly as
            # require_active_provider_api_key skips such providers.
            has_api_key=not env_var or resolve_api_key(env_var) is not None,
            enable_system_trust_store=config.enable_system_trust_store,
        )

    async def _store_credential(
        self, params: SetupStoreCredentialParams, client_info: ClientInfo
    ) -> SetupStoreCredentialResponse:
        orchestrator = await self._orchestrator()
        provider = _named_provider(orchestrator.config, params.provider)
        # Blocking persistence runs inline (not in a thread): the telemetry it
        # emits needs the running loop. The orchestrator built above is reused
        # for that telemetry instead of paying a second config build.
        outcome = persist_api_key(
            resolve_api_key_provider(provider),
            params.api_key,
            orchestrator=orchestrator,
            launch_context=_launch_context(client_info),
            custom_domain=params.custom_domain,
        )
        return _store_response(outcome)

    async def _submit_choices(
        self, params: SetupSubmitChoicesParams
    ) -> SetupSubmitChoicesResponse:
        config = (await self._orchestrator()).config
        provider, provider_drift = _merged_provider(config, params.provider)
        console_drift = (
            params.console_base_url is not None
            and params.console_base_url != config.console_base_url
        )
        vibe_drift = (
            params.vibe_base_url is not None
            and params.vibe_base_url != config.vibe_base_url
        )
        failures: list[str] = []
        if provider_drift or console_drift or vibe_drift:
            result = await persist_provider_credentials(
                ProviderCredentialsPersistRequest(
                    # The wizard always includes the provider; per-field drift
                    # is expressed by the URL fields below.
                    provider=provider,
                    console_base_url=(
                        params.console_base_url if console_drift else None
                    ),
                    vibe_base_url=params.vibe_base_url if vibe_drift else None,
                ),
                reason="onboarding",
            )
            failures = _persist_failures(result)
        if params.theme is not None:
            failures.extend(await self._apply_theme(params.theme))
        if failures:
            return SetupSubmitChoicesResponse(
                outcome="provider_config_error", failures=failures
            )
        return SetupSubmitChoicesResponse(outcome="completed")

    async def _apply_theme(self, theme: str) -> list[str]:
        orchestrator = await self._orchestrator()
        failures = await orchestrator.set_field(
            _THEME_FIELD, theme, reason="onboarding"
        )
        if not failures:
            return []
        for failure in failures:
            logger.error("Failed to persist theme to config", exc_info=failure)
        return ["theme"]


def _seed_provider(config: VibeConfigSchema) -> ProviderConfig:
    """The wizard's provider, mirroring ``OnboardingContext.from_config``."""
    try:
        return config.get_active_provider()
    except ValueError:
        logger.warning(
            "Setup config could not resolve an active provider; "
            "falling back to the default provider so setup can open.",
            exc_info=True,
        )
        return DEFAULT_PROVIDERS[0]


def _provider_view(provider: ProviderConfig) -> SetupProviderView:
    return SetupProviderView(
        name=provider.name,
        api_base=provider.api_base,
        api_key_env_var=provider.api_key_env_var,
        browser_auth_base_url=provider.browser_auth_base_url,
        browser_auth_api_base_url=provider.browser_auth_api_base_url,
        browser_auth_allow_origin_rewrite=provider.browser_auth_allow_origin_rewrite,
    )


def _active_model(config: VibeConfigSchema) -> str:
    try:
        return config.get_active_model().alias
    except ValueError:
        return DEFAULT_ACTIVE_MODEL_CONFIG.alias


def _named_provider(config: VibeConfigSchema, name: str) -> ProviderConfig:
    for provider in (*config.providers, *DEFAULT_PROVIDERS):
        if provider.name == name:
            return provider
    raise RequestFailure(
        ProtocolErrorCode.INVALID_PARAMS, f"Unknown setup provider: {name}"
    )


def _merged_provider(
    config: VibeConfigSchema, view: SetupProviderView | None
) -> tuple[ProviderConfig, bool]:
    """The provider to persist, and whether the wizard's view drifted it.

    The view's fields are merged onto the full resolved provider of the same
    name so the settings the wizard never touches (backend, api_style, ...)
    survive; an unknown name starts a fresh provider. Without a view the
    active provider is passed through untouched, mirroring the wizard's
    always-include-the-provider request.
    """
    if view is None:
        return _seed_provider(config), False
    fields = {
        "api_base": view.api_base,
        "api_key_env_var": view.api_key_env_var,
        "browser_auth_base_url": view.browser_auth_base_url,
        "browser_auth_api_base_url": view.browser_auth_api_base_url,
        "browser_auth_allow_origin_rewrite": view.browser_auth_allow_origin_rewrite,
    }
    current = next(
        (provider for provider in config.providers if provider.name == view.name), None
    )
    if current is None:
        return ProviderConfig(name=view.name, **fields), True
    merged = current.model_copy(update=fields)
    return merged, merged != current


def _persist_failures(result: ProviderCredentialsPersistResult) -> list[str]:
    failures = []
    if not result.provider:
        failures.append("provider")
    if result.console_base_url is False:
        failures.append("console_base_url")
    if result.vibe_base_url is False:
        failures.append("vibe_base_url")
    return failures


def _launch_context(client_info: ClientInfo) -> LaunchContext:
    """The wizard's launch context, built from the initialize handshake.

    ``ClientInfo`` carries the entrypoint and client identity; the agent
    version is this server's, exactly as in Python where the wizard and the
    engine share one distribution.
    """
    return LaunchContext(
        agent_entrypoint=client_info.entrypoint,
        agent_version=__version__,
        client_name=client_info.name,
        client_version=client_info.version,
        terminal_emulator=client_info.terminal_emulator,
    )


def _store_response(outcome: str) -> SetupStoreCredentialResponse:
    if outcome == "completed":
        return SetupStoreCredentialResponse(outcome="completed")
    if outcome.startswith("env_var_error:"):
        return SetupStoreCredentialResponse(
            outcome="env_var_error", detail=outcome.removeprefix("env_var_error:")
        )
    if outcome.startswith("save_error:"):
        return SetupStoreCredentialResponse(
            outcome="save_error", detail=outcome.removeprefix("save_error:")
        )
    # Never silent: an outcome outside the contract is a server bug, not a
    # shape the client should have to guess at.
    raise RuntimeError(f"Unexpected API key persist outcome: {outcome}")
