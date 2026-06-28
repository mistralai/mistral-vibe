from __future__ import annotations

import keyring
from keyring.errors import KeyringError
import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.config import MissingAPIKeyError, ProviderConfig, resolve_api_key
from vibe.core.llm.backend.mistral import MistralBackend
from vibe.core.types import Backend
from vibe.core.utils.keyring import clear_api_key_keyring_cache


def test_resolve_returns_env_value_without_consulting_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUSTOM_API_KEY", "env-key")

    def _fail(service: str, username: str) -> str | None:
        raise AssertionError("keyring must not be consulted when env is set")

    monkeypatch.setattr(keyring, "get_password", _fail)

    assert resolve_api_key("CUSTOM_API_KEY") == "env-key"


def test_resolve_falls_back_to_keyring_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)
    monkeypatch.setattr(
        keyring, "get_password", lambda service, username: "keyring-key"
    )

    assert resolve_api_key("CUSTOM_API_KEY") == "keyring-key"


def test_resolve_caches_keyring_lookup_for_env_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)

    def _get_password(service: str, username: str) -> str:
        calls.append((service, username))
        return "keyring-key"

    monkeypatch.setattr(keyring, "get_password", _get_password)

    assert resolve_api_key("CUSTOM_API_KEY") == "keyring-key"
    assert resolve_api_key("CUSTOM_API_KEY") == "keyring-key"
    assert calls == [("ai.mistral.vibe", "CUSTOM_API_KEY")]


def test_resolve_caches_empty_keyring_lookup_for_env_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)

    def _get_password(service: str, username: str) -> None:
        calls.append((service, username))
        return None

    monkeypatch.setattr(keyring, "get_password", _get_password)

    assert resolve_api_key("CUSTOM_API_KEY") is None
    assert resolve_api_key("CUSTOM_API_KEY") is None
    assert calls == [("ai.mistral.vibe", "CUSTOM_API_KEY"), ("vibe", "CUSTOM_API_KEY")]


def test_resolve_returns_none_when_env_unset_and_keyring_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)
    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)

    assert resolve_api_key("CUSTOM_API_KEY") is None


def test_resolve_returns_none_when_keyring_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)

    def _unavailable(service: str, username: str) -> str | None:
        raise KeyringError("no keyring")

    monkeypatch.setattr(keyring, "get_password", _unavailable)

    assert resolve_api_key("CUSTOM_API_KEY") is None


def test_resolve_does_not_cache_keyring_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)

    def _sometimes_unavailable(service: str, username: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyringError("temporary failure")
        return "keyring-key"

    monkeypatch.setattr(keyring, "get_password", _sometimes_unavailable)

    assert resolve_api_key("CUSTOM_API_KEY") is None
    assert resolve_api_key("CUSTOM_API_KEY") == "keyring-key"
    assert calls == 2


def test_resolve_returns_none_for_empty_env_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(service: str, username: str) -> str | None:
        raise AssertionError("keyring must not be consulted for an empty env key")

    monkeypatch.setattr(keyring, "get_password", _fail)

    assert resolve_api_key("") is None


def test_check_api_key_accepts_keyring_only_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setattr(
        keyring, "get_password", lambda service, username: "keyring-key"
    )

    # Should not raise MissingAPIKeyError despite the env var being unset.
    config = build_test_vibe_config()

    assert config.get_active_provider().api_key_env_var == "MISTRAL_API_KEY"


def test_check_api_key_raises_when_neither_env_nor_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)

    with pytest.raises(MissingAPIKeyError):
        build_test_vibe_config()


def test_mistral_backend_reads_keyring_only_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setattr(
        keyring, "get_password", lambda service, username: "keyring-key"
    )
    provider = ProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai/v1",
        api_key_env_var="MISTRAL_API_KEY",
        backend=Backend.MISTRAL,
    )

    backend = MistralBackend(provider)

    assert backend._api_key == "keyring-key"


def test_vibe_code_api_key_resolves_from_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setattr(
        keyring, "get_password", lambda service, username: "keyring-key"
    )

    config = build_test_vibe_config()

    assert config.vibe_code_api_key == "keyring-key"


def test_vibe_code_api_key_reuses_cached_keyring_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setattr(
        keyring, "get_password", lambda service, username: "keyring-key"
    )
    config = build_test_vibe_config()

    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)

    assert config.vibe_code_api_key == "keyring-key"


def test_vibe_code_api_key_empty_when_cache_cleared_and_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setattr(
        keyring, "get_password", lambda service, username: "keyring-key"
    )
    config = build_test_vibe_config()

    clear_api_key_keyring_cache()
    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)

    assert config.vibe_code_api_key == ""
