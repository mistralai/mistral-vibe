from __future__ import annotations

from typing import Any

import pytest

from vibe.core.config._defaults import DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL
from vibe.core.config.vibe_schema import DEFAULT_PROVIDERS
from vibe.setup import onboarding
from vibe.setup.auth import HttpBrowserSignInGateway
from vibe.setup.onboarding import OnboardingApp
from vibe.setup.onboarding.context import OnboardingContext


def _mistral_context() -> OnboardingContext:
    return OnboardingContext(provider=DEFAULT_PROVIDERS[0])


def _patch_persistence(
    monkeypatch: pytest.MonkeyPatch, persist_provider_success: bool = True
) -> tuple[list[dict[str, Any]], list[Any]]:
    persist_kwargs: list[dict[str, Any]] = []
    saved_providers: list[Any] = []

    def _spy_persist_api_key(*_args: Any, **kwargs: Any) -> str:
        persist_kwargs.append(kwargs)
        return "completed"

    def _spy_persist_provider_to_config(provider: Any) -> bool:
        saved_providers.append(provider)
        return persist_provider_success

    monkeypatch.setattr(onboarding, "persist_api_key", _spy_persist_api_key)
    monkeypatch.setattr(
        onboarding, "persist_provider_to_config", _spy_persist_provider_to_config
    )
    return persist_kwargs, saved_providers


def test_factory_reads_provider_at_call_time() -> None:
    app = OnboardingApp(_mistral_context())
    app.apply_custom_domain("https://custom.example.com")

    assert app._browser_sign_in_service_factory is not None
    service = app._browser_sign_in_service_factory()

    gateway = service._gateway
    assert isinstance(gateway, HttpBrowserSignInGateway)
    assert gateway._browser_base_url == "https://custom.example.com"
    assert gateway._api_base_url == "https://custom.example.com/api"


def test_mistral_default_domain_resets_custom_auth_urls() -> None:
    app = OnboardingApp(_mistral_context())
    app.apply_custom_domain("https://toto.com")
    assert app._provider.browser_auth_base_url == "https://toto.com"

    app.apply_mistral_default_domain()

    assert app._browser_sign_in_service_factory is not None
    gateway = app._browser_sign_in_service_factory()._gateway
    assert isinstance(gateway, HttpBrowserSignInGateway)
    assert gateway._browser_base_url == "https://console.mistral.ai"
    assert gateway._api_base_url == "https://console.mistral.ai/api"


def test_persist_credentials_customized_saves_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _persist_kwargs, saved_providers = _patch_persistence(monkeypatch)

    app = OnboardingApp(_mistral_context())
    app.apply_custom_domain("https://custom.example.com")
    result = app.persist_credentials("secret-key")

    assert result == "completed"
    assert len(saved_providers) == 1
    assert saved_providers[0].browser_auth_base_url == "https://custom.example.com"


def test_persist_credentials_persists_in_memory_provider_when_env_var_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # When the active provider has an empty api_key_env_var, resolve_api_key_provider
    # falls back to the default Mistral provider for key storage. The provider
    # persisted to config.toml must still be the in-memory one carrying the
    # custom domain, not the default with reset URLs.
    _persist_kwargs, saved_providers = _patch_persistence(monkeypatch)

    provider = DEFAULT_PROVIDERS[0].model_copy(update={"api_key_env_var": ""})
    app = OnboardingApp(OnboardingContext(provider=provider))
    app.apply_custom_domain("https://custom.example.com")
    result = app.persist_credentials("secret-key")

    assert result == "completed"
    assert len(saved_providers) == 1
    assert saved_providers[0].browser_auth_base_url == "https://custom.example.com"
    assert (
        saved_providers[0].browser_auth_api_base_url == "https://custom.example.com/api"
    )


def test_persist_credentials_mistral_default_saves_provider_without_custom_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _persist_kwargs, saved_providers = _patch_persistence(monkeypatch)

    custom_provider = DEFAULT_PROVIDERS[0].model_copy(
        update={
            "browser_auth_base_url": "https://custom.example.com",
            "browser_auth_api_base_url": "https://custom.example.com/api",
        }
    )
    app = OnboardingApp(OnboardingContext(provider=custom_provider))
    app.apply_mistral_default_domain()
    result = app.persist_credentials("secret-key")

    assert result == "completed"
    assert len(saved_providers) == 1
    assert (
        saved_providers[0].browser_auth_base_url
        == DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL
    )


def test_persist_credentials_not_customized_does_not_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _persist_kwargs, saved_providers = _patch_persistence(monkeypatch)

    app = OnboardingApp(_mistral_context())
    result = app.persist_credentials("secret-key")

    assert result == "completed"
    assert saved_providers == []


def test_persist_credentials_returns_error_when_provider_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _persist_kwargs, saved_providers = _patch_persistence(
        monkeypatch, persist_provider_success=False
    )

    app = OnboardingApp(_mistral_context())
    app.apply_custom_domain("https://custom.example.com")
    result = app.persist_credentials("secret-key")

    assert result == "provider_config_error:failed to persist provider config"
    assert len(saved_providers) == 1
