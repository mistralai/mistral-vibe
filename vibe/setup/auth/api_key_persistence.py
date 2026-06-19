from __future__ import annotations

import os

from dotenv import set_key, unset_key
import keyring
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

from vibe.core.config import DEFAULT_PROVIDERS, ProviderConfig, VibeConfig
from vibe.core.logger import logger
from vibe.core.paths import GLOBAL_ENV_FILE

_KEYRING_SERVICE = "vibe"
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import EntrypointMetadata
from vibe.core.types import Backend


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
        keyring.set_password(_KEYRING_SERVICE, env_key, api_key)
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
            telemetry = TelemetryClient(
                config_getter=VibeConfig,
                entrypoint_metadata_getter=lambda: entrypoint_metadata,
            )
            telemetry.send_onboarding_api_key_added()
        except Exception:
            pass
    return "completed"


def remove_api_key(provider: ProviderConfig) -> None:
    env_key = provider.api_key_env_var
    if not env_key:
        raise ValueError("Cannot remove API key without an environment variable name")
    keyring_error: KeyringError | None = None

    try:
        keyring.delete_password(_KEYRING_SERVICE, env_key)
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
