from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import set_key, unset_key
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

from vibe.core.config import DEFAULT_PROVIDERS, ProviderConfig, VibeConfigSchema
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import LaunchContext
from vibe.core.types import Backend
from vibe.core.utils.concurrency import run_sync
from vibe.observability.logging import logger
from vibe.utils.keyring import delete_api_key_from_keyring, set_api_key_in_keyring


def _save_api_key_to_env_file(env_key: str, api_key: str) -> None:
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    set_key(GLOBAL_ENV_FILE.path, env_key, api_key)


def _remove_api_key_from_env_file(env_key: str) -> None:
    if not GLOBAL_ENV_FILE.path.exists():
        return
    unset_key(GLOBAL_ENV_FILE.path, env_key)


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


async def apply_provider_to_config(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    provider: ProviderConfig,
    *,
    reason: str = "onboarding",
) -> bool:
    # exclude_defaults avoids pinning today's field defaults (api_style,
    # reasoning_field_name, empty project_id/region, extra_headers) into config.toml.
    payload = provider.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    failures = await orchestrator.upsert_field(
        "/providers", key_field="name", value=payload, reason=reason
    )
    if failures:
        for failure in failures:
            logger.error(
                "Failed to persist provider to config name=%s",
                provider.name,
                exc_info=failure,
            )
        return False
    return True


async def apply_console_base_url(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    console_base_url: str,
    *,
    reason: str = "onboarding",
) -> bool:
    failures = await orchestrator.set_field(
        "/console_base_url", console_base_url, reason=reason
    )
    if failures:
        for failure in failures:
            logger.error(
                "Failed to persist console_base_url to config", exc_info=failure
            )
        return False
    return True


async def apply_vibe_base_url(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    vibe_base_url: str,
    *,
    reason: str = "onboarding",
) -> bool:
    failures = await orchestrator.set_field(
        "/vibe_base_url", vibe_base_url, reason=reason
    )
    if failures:
        for failure in failures:
            logger.error("Failed to persist vibe_base_url to config", exc_info=failure)
        return False
    return True


@dataclass(frozen=True, slots=True)
class ProviderCredentialsPersistRequest:
    """One batch of provider-related config writes.

    ``console_base_url`` / ``vibe_base_url`` are ``None`` when the caller has
    no drift to persist for that field — the default persister leaves them
    untouched rather than writing the current value back.
    """

    provider: ProviderConfig
    console_base_url: str | None = None
    vibe_base_url: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderCredentialsPersistResult:
    """Per-field outcome of a batched persist call.

    ``None`` means "not requested" (caller passed ``None`` on the request);
    ``True``/``False`` means "attempted, succeeded/failed".
    """

    provider: bool
    console_base_url: bool | None = None
    vibe_base_url: bool | None = None

    @property
    def all_requested_succeeded(self) -> bool:
        return (
            self.provider
            and self.console_base_url is not False
            and self.vibe_base_url is not False
        )

    def first_failure(self) -> str | None:
        if not self.provider:
            return "provider"
        if self.console_base_url is False:
            return "console_base_url"
        if self.vibe_base_url is False:
            return "vibe_base_url"
        return None


def persist_provider_to_config(provider: ProviderConfig) -> bool:
    """Sync wrapper around ``apply_provider_to_config`` for legacy callers.

    Production code should prefer :func:`persist_provider_credentials`, which
    batches multiple field writes against a single orchestrator.
    """
    orchestrator = run_sync(build_default_orchestrator())
    return run_sync(apply_provider_to_config(orchestrator, provider))


async def persist_provider_credentials(
    request: ProviderCredentialsPersistRequest, *, reason: str = "onboarding"
) -> ProviderCredentialsPersistResult:
    """Persist provider + optional console/vibe URLs against a single orchestrator.

    Writes run sequentially against one orchestrator instance so we don't pay
    three separate config-build round-trips. Partial failure is reported per
    field so callers can tell the user which pieces of state did/didn't land.
    """
    orchestrator = await build_default_orchestrator()
    provider_ok = await apply_provider_to_config(
        orchestrator, request.provider, reason=reason
    )
    console_ok: bool | None = None
    if request.console_base_url is not None:
        console_ok = await apply_console_base_url(
            orchestrator, request.console_base_url, reason=reason
        )
    vibe_ok: bool | None = None
    if request.vibe_base_url is not None:
        vibe_ok = await apply_vibe_base_url(
            orchestrator, request.vibe_base_url, reason=reason
        )
    return ProviderCredentialsPersistResult(
        provider=provider_ok, console_base_url=console_ok, vibe_base_url=vibe_ok
    )


def persist_api_key(
    provider: ProviderConfig,
    api_key: str,
    *,
    launch_context: LaunchContext | None = None,
    custom_domain: bool = False,
) -> str:
    env_key = provider.api_key_env_var
    if not env_key:
        return "env_var_error:<empty>"
    try:
        os.environ[env_key] = api_key
    except ValueError:
        return f"env_var_error:{env_key}"
    try:
        set_api_key_in_keyring(env_key, api_key)
    except KeyringError:
        try:
            _save_api_key_to_env_file(env_key, api_key)
        except (OSError, ValueError) as err:
            return f"save_error:{err}"
    else:
        # The key is safely stored in the keyring; drop any stale plaintext copy.
        try:
            _remove_api_key_from_env_file(env_key)
        except (OSError, ValueError) as err:
            logger.error(
                "Failed to remove stale plaintext API key from env file", exc_info=err
            )
    if provider.backend == Backend.MISTRAL:
        try:
            orchestrator = run_sync(build_default_orchestrator())
            telemetry = TelemetryClient(
                config_getter=lambda: orchestrator.config, launch_context=launch_context
            )
            telemetry.send_onboarding_api_key_added(custom_domain=custom_domain)
        except Exception:
            pass
    return "completed"


def remove_api_key(provider: ProviderConfig) -> None:
    env_key = provider.api_key_env_var
    if not env_key:
        raise ValueError("Cannot remove API key without an environment variable name")
    keyring_error: KeyringError | None = None

    try:
        delete_api_key_from_keyring(env_key)
    except (NoKeyringError, PasswordDeleteError):
        # No keyring backend, or nothing stored to remove: both are no-ops for sign-out.
        pass
    except KeyringError as exc:
        # Deletion was attempted but failed still clear the other copies, then
        # surface the failure so sign-out does not look successful while the
        # credential is still in the keyring.
        keyring_error = exc

    _remove_api_key_from_env_file(env_key)
    os.environ.pop(env_key, None)
    if keyring_error is not None:
        raise keyring_error
