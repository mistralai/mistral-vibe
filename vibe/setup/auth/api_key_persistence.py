from __future__ import annotations

import os

from dotenv import set_key

from vibe.core.config import DEFAULT_PROVIDERS, ProviderConfig, VibeConfig
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import EntrypointMetadata
from vibe.core.types import Backend


def _save_api_key_to_env_file(env_key: str, api_key: str) -> None:
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    set_key(GLOBAL_ENV_FILE.path, env_key, api_key)


def _get_mistral_provider() -> ProviderConfig:
    return next(
        provider for provider in DEFAULT_PROVIDERS if provider.name == "mistral"
    )


def _load_onboarding_provider() -> ProviderConfig:
    from vibe.setup.onboarding.context import OnboardingContext

    return OnboardingContext.load().provider


def resolve_api_key_provider(provider: ProviderConfig | None = None) -> ProviderConfig:
    resolved_provider = provider or _load_onboarding_provider()
    if resolved_provider.api_key_env_var:
        return resolved_provider
    return _get_mistral_provider()


def persist_api_key(
    provider: ProviderConfig,
    api_key: str,
    *,
    entrypoint_metadata: EntrypointMetadata | None = None,
) -> str:
    env_key = provider.api_key_env_var
    if not env_key:
        return "env_var_error:<empty>"
    try:
        os.environ[env_key] = api_key
    except ValueError:
        return f"env_var_error:{env_key}"
    try:
        _save_api_key_to_env_file(env_key, api_key)
    except (OSError, ValueError) as err:
        return f"save_error:{err}"
    if provider.backend == Backend.MISTRAL:
        try:
            telemetry = TelemetryClient(
                config_getter=VibeConfig,
                entrypoint_metadata_getter=lambda: entrypoint_metadata,
            )
            telemetry.send_onboarding_api_key_added()
        except Exception:
            pass
    return "completed"
