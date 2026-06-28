from __future__ import annotations

import os

from dotenv import dotenv_values, set_key
import keyring
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError
import pytest

from vibe.core.config import ProviderConfig, resolve_api_key
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.types import Backend
from vibe.setup.auth.api_key_persistence import persist_api_key, remove_api_key


def _provider(*, api_key_env_var: str = "CUSTOM_API_KEY") -> ProviderConfig:
    # Backend.GENERIC keeps onboarding telemetry out of these unit tests.
    return ProviderConfig(
        name="custom",
        api_base="https://custom.example/v1",
        api_key_env_var=api_key_env_var,
        backend=Backend.GENERIC,
    )


def test_persist_stores_in_keyring_and_clears_stale_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: dict[str, str] = {}
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, username, password: stored.__setitem__(username, password),
    )
    # A stale plaintext copy that should be dropped after the keyring write.
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    set_key(GLOBAL_ENV_FILE.path, "CUSTOM_API_KEY", "old-key")

    result = persist_api_key(_provider(), "new-key")

    assert result == "completed"
    assert stored == {"CUSTOM_API_KEY": "new-key"}
    assert os.environ["CUSTOM_API_KEY"] == "new-key"
    assert "CUSTOM_API_KEY" not in dotenv_values(GLOBAL_ENV_FILE.path)


def test_persist_updates_cached_keyring_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)
    monkeypatch.setattr(keyring, "get_password", lambda service, username: "old-key")
    monkeypatch.setattr(
        keyring, "set_password", lambda service, username, password: None
    )

    assert resolve_api_key("CUSTOM_API_KEY") == "old-key"

    result = persist_api_key(_provider(), "new-key")
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)

    assert result == "completed"
    assert resolve_api_key("CUSTOM_API_KEY") == "new-key"


def test_persist_falls_back_to_env_when_keyring_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)

    def _unavailable(service: str, username: str, password: str) -> None:
        raise KeyringError("no keyring")

    monkeypatch.setattr(keyring, "set_password", _unavailable)

    result = persist_api_key(_provider(), "new-key")

    assert result == "completed"
    assert os.environ["CUSTOM_API_KEY"] == "new-key"
    assert dotenv_values(GLOBAL_ENV_FILE.path)["CUSTOM_API_KEY"] == "new-key"


def test_persist_fallback_clears_stale_cached_keyring_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)
    monkeypatch.setattr(keyring, "get_password", lambda service, username: "old-key")

    def _unavailable(service: str, username: str, password: str) -> None:
        raise KeyringError("no keyring")

    assert resolve_api_key("CUSTOM_API_KEY") == "old-key"

    monkeypatch.setattr(keyring, "set_password", _unavailable)
    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)

    result = persist_api_key(_provider(), "new-key")
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)

    assert result == "completed"
    assert resolve_api_key("CUSTOM_API_KEY") is None


def test_persist_returns_env_var_error_for_empty_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(service: str, username: str, password: str) -> None:
        raise AssertionError("keyring should not be used for an empty env var")

    monkeypatch.setattr(keyring, "set_password", _fail)

    result = persist_api_key(_provider(api_key_env_var=""), "new-key")

    assert result == "env_var_error:<empty>"


def test_remove_deletes_keyring_env_and_process_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[tuple[str, str]] = []
    monkeypatch.setenv("CUSTOM_API_KEY", "live-key")
    monkeypatch.setattr(
        keyring,
        "delete_password",
        lambda service, username: deleted.append((service, username)),
    )
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    set_key(GLOBAL_ENV_FILE.path, "CUSTOM_API_KEY", "file-key")

    remove_api_key(_provider())

    assert deleted == [
        ("ai.mistral.vibe", "CUSTOM_API_KEY"),
        ("vibe", "CUSTOM_API_KEY"),
    ]
    assert "CUSTOM_API_KEY" not in dotenv_values(GLOBAL_ENV_FILE.path)
    assert "CUSTOM_API_KEY" not in os.environ


def test_remove_clears_cached_keyring_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)
    monkeypatch.setattr(keyring, "get_password", lambda service, username: "live-key")
    monkeypatch.setattr(keyring, "delete_password", lambda service, username: None)

    assert resolve_api_key("CUSTOM_API_KEY") == "live-key"

    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)
    remove_api_key(_provider())

    assert resolve_api_key("CUSTOM_API_KEY") is None


def test_remove_ignores_keyring_unavailable_and_still_clears_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUSTOM_API_KEY", "live-key")

    def _unavailable(service: str, username: str) -> None:
        raise NoKeyringError("no keyring")

    monkeypatch.setattr(keyring, "delete_password", _unavailable)
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    set_key(GLOBAL_ENV_FILE.path, "CUSTOM_API_KEY", "file-key")

    remove_api_key(_provider())

    assert "CUSTOM_API_KEY" not in dotenv_values(GLOBAL_ENV_FILE.path)
    assert "CUSTOM_API_KEY" not in os.environ


def test_remove_ignores_missing_keyring_entry_and_still_clears_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUSTOM_API_KEY", "live-key")

    def _missing(service: str, username: str) -> None:
        raise PasswordDeleteError()

    monkeypatch.setattr(keyring, "delete_password", _missing)
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    set_key(GLOBAL_ENV_FILE.path, "CUSTOM_API_KEY", "file-key")

    remove_api_key(_provider())

    assert "CUSTOM_API_KEY" not in dotenv_values(GLOBAL_ENV_FILE.path)
    assert "CUSTOM_API_KEY" not in os.environ


def test_remove_surfaces_keyring_operation_error_but_still_clears_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUSTOM_API_KEY", "live-key")

    def _failed(service: str, username: str) -> None:
        raise KeyringError("delete failed")

    monkeypatch.setattr(keyring, "delete_password", _failed)
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    set_key(GLOBAL_ENV_FILE.path, "CUSTOM_API_KEY", "file-key")

    with pytest.raises(KeyringError, match="delete failed"):
        remove_api_key(_provider())

    assert "CUSTOM_API_KEY" not in dotenv_values(GLOBAL_ENV_FILE.path)
    assert "CUSTOM_API_KEY" not in os.environ
