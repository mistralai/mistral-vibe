from __future__ import annotations

from pathlib import Path

import keyring
import pytest

from vibe.core.config import DEFAULT_MISTRAL_API_ENV_KEY, ProviderConfig
from vibe.core.types import Backend
from vibe.setup.auth import AuthState, AuthStateKind, assess_auth_state


@pytest.fixture(autouse=True)
def disable_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)


def _mistral_provider(
    *, api_key_env_var: str = DEFAULT_MISTRAL_API_ENV_KEY
) -> ProviderConfig:
    return ProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai/v1",
        api_key_env_var=api_key_env_var,
        browser_auth_base_url="https://console.mistral.ai",
        browser_auth_api_base_url="https://console.mistral.ai/api",
        backend=Backend.MISTRAL,
    )


def _generic_provider(
    *, name: str = "custom", api_key_env_var: str = "CUSTOM_API_KEY"
) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        api_base="https://custom.example/v1",
        api_key_env_var=api_key_env_var,
        backend=Backend.GENERIC,
    )


def _write_env_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_assess_signed_out_when_provider_requires_key_without_any_source(
    tmp_path: Path,
) -> None:
    state = assess_auth_state(
        _mistral_provider(), env_path=tmp_path / ".env", environ={}
    )

    assert state == AuthState(
        kind=AuthStateKind.SIGNED_OUT,
        can_use_active_provider=False,
        sign_out_available=False,
        env_key=DEFAULT_MISTRAL_API_ENV_KEY,
    )


def test_assess_auth_not_required_when_provider_has_no_api_key_env_var(
    tmp_path: Path,
) -> None:
    state = assess_auth_state(
        _generic_provider(name="llamacpp", api_key_env_var=""),
        env_path=tmp_path / ".env",
        environ={},
    )

    assert state == AuthState(
        kind=AuthStateKind.AUTH_NOT_REQUIRED,
        can_use_active_provider=True,
        sign_out_available=False,
        env_key=None,
    )


def test_assess_vibe_home_env_file_when_default_key_is_in_dotenv(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    _write_env_file(env_path, f"{DEFAULT_MISTRAL_API_ENV_KEY}=file-key\n")

    state = assess_auth_state(_mistral_provider(), env_path=env_path, environ={})

    assert state == AuthState(
        kind=AuthStateKind.VIBE_HOME_ENV_FILE,
        can_use_active_provider=True,
        sign_out_available=True,
        env_key=DEFAULT_MISTRAL_API_ENV_KEY,
    )


def test_assess_process_env_when_default_key_is_only_in_process_env(
    tmp_path: Path,
) -> None:
    state = assess_auth_state(
        _mistral_provider(),
        env_path=tmp_path / ".env",
        environ={DEFAULT_MISTRAL_API_ENV_KEY: "process-key"},
    )

    assert state == AuthState(
        kind=AuthStateKind.PROCESS_ENV,
        can_use_active_provider=True,
        sign_out_available=False,
        env_key=DEFAULT_MISTRAL_API_ENV_KEY,
    )


def test_assess_process_env_when_process_env_and_dotenv_both_exist(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    _write_env_file(env_path, f"{DEFAULT_MISTRAL_API_ENV_KEY}=file-key\n")

    state = assess_auth_state(
        _mistral_provider(),
        env_path=env_path,
        environ={DEFAULT_MISTRAL_API_ENV_KEY: "file-key"},
        process_env_had_value_before_dotenv_load=True,
    )

    assert state == AuthState(
        kind=AuthStateKind.PROCESS_ENV,
        can_use_active_provider=True,
        sign_out_available=False,
        env_key=DEFAULT_MISTRAL_API_ENV_KEY,
    )


def test_assess_vibe_home_env_file_when_dotenv_did_not_override_process_env(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    _write_env_file(env_path, f"{DEFAULT_MISTRAL_API_ENV_KEY}=file-key\n")

    state = assess_auth_state(
        _mistral_provider(),
        env_path=env_path,
        environ={DEFAULT_MISTRAL_API_ENV_KEY: "file-key"},
        process_env_had_value_before_dotenv_load=False,
    )

    assert state == AuthState(
        kind=AuthStateKind.VIBE_HOME_ENV_FILE,
        can_use_active_provider=True,
        sign_out_available=True,
        env_key=DEFAULT_MISTRAL_API_ENV_KEY,
    )


def test_assess_vibe_home_env_file_is_sign_out_eligible_even_without_browser_sign_in(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    _write_env_file(env_path, f"{DEFAULT_MISTRAL_API_ENV_KEY}=file-key\n")
    provider = ProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai/v1",
        api_key_env_var=DEFAULT_MISTRAL_API_ENV_KEY,
        backend=Backend.GENERIC,
    )
    assert not provider.supports_browser_sign_in

    state = assess_auth_state(provider, env_path=env_path, environ={})

    assert state == AuthState(
        kind=AuthStateKind.VIBE_HOME_ENV_FILE,
        can_use_active_provider=True,
        sign_out_available=True,
        env_key=DEFAULT_MISTRAL_API_ENV_KEY,
    )


def test_assess_unsupported_provider_when_custom_env_key_is_set_in_dotenv(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    _write_env_file(env_path, "CUSTOM_API_KEY=file-key\n")

    state = assess_auth_state(
        _mistral_provider(api_key_env_var="CUSTOM_API_KEY"),
        env_path=env_path,
        environ={},
    )

    assert state == AuthState(
        kind=AuthStateKind.UNSUPPORTED_PROVIDER,
        can_use_active_provider=True,
        sign_out_available=False,
        env_key="CUSTOM_API_KEY",
    )


def test_assess_unsupported_provider_when_custom_provider_uses_process_env(
    tmp_path: Path,
) -> None:
    state = assess_auth_state(
        _generic_provider(),
        env_path=tmp_path / ".env",
        environ={"CUSTOM_API_KEY": "process-key"},
    )

    assert state == AuthState(
        kind=AuthStateKind.UNSUPPORTED_PROVIDER,
        can_use_active_provider=True,
        sign_out_available=False,
        env_key="CUSTOM_API_KEY",
    )


def test_assess_empty_dotenv_value_as_signed_out(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    _write_env_file(env_path, f"{DEFAULT_MISTRAL_API_ENV_KEY}=\n")

    state = assess_auth_state(_mistral_provider(), env_path=env_path, environ={})

    assert state == AuthState(
        kind=AuthStateKind.SIGNED_OUT,
        can_use_active_provider=False,
        sign_out_available=False,
        env_key=DEFAULT_MISTRAL_API_ENV_KEY,
    )


def test_assess_os_keyring_when_default_key_is_in_keyring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        keyring, "get_password", lambda service, username: "keyring-key"
    )

    state = assess_auth_state(
        _mistral_provider(), env_path=tmp_path / ".env", environ={}
    )

    assert state == AuthState(
        kind=AuthStateKind.OS_KEYRING,
        can_use_active_provider=True,
        sign_out_available=True,
        env_key=DEFAULT_MISTRAL_API_ENV_KEY,
    )


def test_assess_vibe_home_env_file_when_dotenv_and_keyring_both_have_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # resolve_api_key reads the .env-injected os.environ value before the keyring,
    # so an overlap must be reported as the .env file, not OS keyring.
    monkeypatch.setattr(
        keyring, "get_password", lambda service, username: "keyring-key"
    )
    env_path = tmp_path / ".env"
    _write_env_file(env_path, f"{DEFAULT_MISTRAL_API_ENV_KEY}=file-key\n")

    state = assess_auth_state(_mistral_provider(), env_path=env_path, environ={})

    assert state == AuthState(
        kind=AuthStateKind.VIBE_HOME_ENV_FILE,
        can_use_active_provider=True,
        sign_out_available=True,
        env_key=DEFAULT_MISTRAL_API_ENV_KEY,
    )


def test_assess_unsupported_provider_when_custom_key_is_in_keyring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        keyring, "get_password", lambda service, username: "keyring-key"
    )

    state = assess_auth_state(
        _generic_provider(), env_path=tmp_path / ".env", environ={}
    )

    assert state == AuthState(
        kind=AuthStateKind.UNSUPPORTED_PROVIDER,
        can_use_active_provider=True,
        sign_out_available=False,
        env_key="CUSTOM_API_KEY",
    )
