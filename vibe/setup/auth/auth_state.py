from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum, auto
import os
from pathlib import Path

from dotenv import dotenv_values

from vibe.core.config import DEFAULT_MISTRAL_API_ENV_KEY, ProviderConfig
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.utils.keyring import get_api_key_from_keyring


class AuthStateKind(StrEnum):
    SIGNED_OUT = auto()
    AUTH_NOT_REQUIRED = auto()
    OS_KEYRING = auto()
    VIBE_HOME_ENV_FILE = auto()
    PROCESS_ENV = auto()
    UNSUPPORTED_PROVIDER = auto()


@dataclass(frozen=True, slots=True)
class _AuthEnvSnapshot:
    env_key: str
    current_process_has_value: bool
    keyring_has_value: bool
    dotenv_has_value: bool
    process_env_had_value_before_dotenv_load: bool


@dataclass(frozen=True, slots=True)
class AuthState:
    kind: AuthStateKind
    can_use_active_provider: bool
    sign_out_available: bool
    env_key: str | None


def _has_value(value: str | None) -> bool:
    return bool(value)


def _dotenv_has_value(env_path: Path, env_key: str) -> bool:
    if not env_path.is_file() and not env_path.is_fifo():
        return False

    value = dotenv_values(env_path).get(env_key)
    if not isinstance(value, str):
        return False
    return _has_value(value)


def _supports_vibe_owned_sign_out(provider: ProviderConfig) -> bool:
    return provider.api_key_env_var == DEFAULT_MISTRAL_API_ENV_KEY


def _auth_state(
    kind: AuthStateKind,
    *,
    can_use_active_provider: bool,
    sign_out_available: bool = False,
    env_key: str | None = None,
) -> AuthState:
    return AuthState(
        kind=kind,
        can_use_active_provider=can_use_active_provider,
        sign_out_available=sign_out_available,
        env_key=env_key,
    )


def _capture_auth_env_snapshot(
    env_key: str,
    *,
    env_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    process_env_had_value_before_dotenv_load: bool = False,
) -> _AuthEnvSnapshot:
    resolved_env_path = env_path if env_path is not None else GLOBAL_ENV_FILE.path
    resolved_environ = environ if environ is not None else os.environ
    keyring_has_value = _has_value(get_api_key_from_keyring(env_key))

    return _AuthEnvSnapshot(
        env_key=env_key,
        current_process_has_value=_has_value(resolved_environ.get(env_key)),
        keyring_has_value=keyring_has_value,
        dotenv_has_value=_dotenv_has_value(resolved_env_path, env_key),
        process_env_had_value_before_dotenv_load=process_env_had_value_before_dotenv_load,
    )


def assess_auth_state(
    provider: ProviderConfig,
    *,
    env_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    process_env_had_value_before_dotenv_load: bool = False,
) -> AuthState:
    env_key = provider.api_key_env_var
    if not env_key:
        return _auth_state(
            AuthStateKind.AUTH_NOT_REQUIRED, can_use_active_provider=True
        )

    auth_snapshot = _capture_auth_env_snapshot(
        env_key,
        env_path=env_path,
        environ=environ,
        process_env_had_value_before_dotenv_load=process_env_had_value_before_dotenv_load,
    )
    if (
        not auth_snapshot.current_process_has_value
        and not auth_snapshot.keyring_has_value
        and not auth_snapshot.dotenv_has_value
    ):
        return _auth_state(
            AuthStateKind.SIGNED_OUT, can_use_active_provider=False, env_key=env_key
        )

    if not _supports_vibe_owned_sign_out(provider):
        return _auth_state(
            AuthStateKind.UNSUPPORTED_PROVIDER,
            can_use_active_provider=True,
            env_key=env_key,
        )

    if auth_snapshot.process_env_had_value_before_dotenv_load:
        kind = AuthStateKind.PROCESS_ENV
        sign_out_available = False
    elif auth_snapshot.dotenv_has_value:
        # load_dotenv_values injects the .env value into os.environ, and
        # resolve_api_key reads os.environ before the keyring. So a .env entry is
        # the active credential even when the keyring also holds one, and the
        # reported state must reflect the .env file rather than the keyring.
        kind = AuthStateKind.VIBE_HOME_ENV_FILE
        sign_out_available = True
    elif auth_snapshot.keyring_has_value:
        kind = AuthStateKind.OS_KEYRING
        sign_out_available = True
    elif auth_snapshot.current_process_has_value:
        kind = AuthStateKind.PROCESS_ENV
        sign_out_available = False
    else:
        raise AssertionError("assess_auth_state reached unreachable state")

    return _auth_state(
        kind,
        can_use_active_provider=True,
        sign_out_available=sign_out_available,
        env_key=env_key,
    )


__all__ = ["AuthState", "AuthStateKind", "assess_auth_state"]
