from __future__ import annotations

from typing import Any

import pytest

from vibe.core.config._defaults import (
    DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
    DEFAULT_MISTRAL_SERVER_URL,
    DEFAULT_VIBE_BASE_URL,
)
from vibe.core.config.vibe_schema import DEFAULT_PROVIDERS
from vibe.setup import onboarding
from vibe.setup.auth import HttpBrowserSignInGateway
from vibe.setup.auth.api_key_persistence import (
    ProviderCredentialsPersistRequest,
    ProviderCredentialsPersistResult,
)
from vibe.setup.onboarding import OnboardingApp
from vibe.setup.onboarding.context import OnboardingContext


def _mistral_context() -> OnboardingContext:
    return OnboardingContext(provider=DEFAULT_PROVIDERS[0])


def _patch_persistence(
    monkeypatch: pytest.MonkeyPatch, persist_provider_success: bool = True
) -> tuple[list[dict[str, Any]], list[ProviderCredentialsPersistRequest]]:
    persist_kwargs: list[dict[str, Any]] = []
    saved_requests: list[ProviderCredentialsPersistRequest] = []

    def _spy_persist_api_key(*_args: Any, **kwargs: Any) -> str:
        persist_kwargs.append(kwargs)
        return "completed"

    async def _spy_persist_provider_credentials(
        request: ProviderCredentialsPersistRequest,
    ) -> ProviderCredentialsPersistResult:
        saved_requests.append(request)
        return ProviderCredentialsPersistResult(
            provider=persist_provider_success,
            console_base_url=True if request.console_base_url is not None else None,
            vibe_base_url=True if request.vibe_base_url is not None else None,
        )

    monkeypatch.setattr(onboarding, "persist_api_key", _spy_persist_api_key)
    monkeypatch.setattr(
        onboarding, "persist_provider_credentials", _spy_persist_provider_credentials
    )
    return persist_kwargs, saved_requests


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


@pytest.mark.asyncio
async def test_persist_credentials_customized_saves_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _persist_kwargs, saved_requests = _patch_persistence(monkeypatch)

    app = OnboardingApp(_mistral_context())
    app.apply_custom_domain("https://custom.example.com")
    result = await app.persist_credentials("secret-key")

    assert result == "completed"
    assert len(saved_requests) == 1
    assert (
        saved_requests[0].provider.browser_auth_base_url == "https://custom.example.com"
    )


@pytest.mark.asyncio
async def test_persist_credentials_persists_in_memory_provider_when_env_var_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # When the active provider has an empty api_key_env_var, resolve_api_key_provider
    # falls back to the default Mistral provider for key storage. The provider
    # persisted to config.toml must still be the in-memory one carrying the
    # custom domain, not the default with reset URLs.
    _persist_kwargs, saved_requests = _patch_persistence(monkeypatch)

    provider = DEFAULT_PROVIDERS[0].model_copy(update={"api_key_env_var": ""})
    app = OnboardingApp(OnboardingContext(provider=provider))
    app.apply_custom_domain("https://custom.example.com")
    result = await app.persist_credentials("secret-key")

    assert result == "completed"
    assert len(saved_requests) == 1
    assert (
        saved_requests[0].provider.browser_auth_base_url == "https://custom.example.com"
    )
    assert (
        saved_requests[0].provider.browser_auth_api_base_url
        == "https://custom.example.com/api"
    )


@pytest.mark.asyncio
async def test_persist_credentials_mistral_default_saves_provider_without_custom_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _persist_kwargs, saved_requests = _patch_persistence(monkeypatch)

    custom_provider = DEFAULT_PROVIDERS[0].model_copy(
        update={
            "browser_auth_base_url": "https://custom.example.com",
            "browser_auth_api_base_url": "https://custom.example.com/api",
        }
    )
    app = OnboardingApp(OnboardingContext(provider=custom_provider))
    app.apply_mistral_default_domain()
    result = await app.persist_credentials("secret-key")

    assert result == "completed"
    assert len(saved_requests) == 1
    assert (
        saved_requests[0].provider.browser_auth_base_url
        == DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL
    )


@pytest.mark.asyncio
async def test_persist_credentials_not_customized_does_not_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _persist_kwargs, saved_requests = _patch_persistence(monkeypatch)

    app = OnboardingApp(_mistral_context())
    result = await app.persist_credentials("secret-key")

    assert result == "completed"
    assert saved_requests == []


@pytest.mark.asyncio
async def test_persist_credentials_returns_error_when_provider_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _persist_kwargs, saved_requests = _patch_persistence(
        monkeypatch, persist_provider_success=False
    )

    app = OnboardingApp(_mistral_context())
    app.apply_custom_domain("https://custom.example.com")
    result = await app.persist_credentials("secret-key")

    assert result == "provider_config_error:failed to persist provider"
    assert len(saved_requests) == 1


@pytest.mark.asyncio
async def test_persist_credentials_on_prem_applies_tenant_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On-prem sign-in: after api key is saved, whoami-derived domains land
    on the persist request and drive all three field writes.
    """
    _persist_kwargs, saved_requests = _patch_persistence(monkeypatch)

    async def _resolver(
        provider: Any, _console: str, _key: str, _current_vibe: str
    ) -> tuple[Any, str]:
        return (
            provider.model_copy(update={"api_base": "https://api.acme.internal/v1"}),
            "https://chat.acme.internal",
        )

    monkeypatch.setattr(onboarding, "resolve_tenant_domains", _resolver)

    app = OnboardingApp(_mistral_context())
    app.apply_custom_domain("https://console.acme.internal")
    result = await app.persist_credentials("secret-key")

    assert result == "completed"
    assert len(saved_requests) == 1
    request = saved_requests[0]
    assert request.provider.api_base == "https://api.acme.internal/v1"
    assert request.console_base_url == "https://console.acme.internal"
    assert request.vibe_base_url == "https://chat.acme.internal"


@pytest.mark.asyncio
async def test_persist_credentials_public_console_does_not_call_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _persist_kwargs, _saved_requests = _patch_persistence(monkeypatch)
    resolver_calls: list[str] = []

    async def _resolver(
        provider: Any, console: str, _key: str, current_vibe: str
    ) -> tuple[Any, str]:
        resolver_calls.append(console)
        return provider, current_vibe

    monkeypatch.setattr(onboarding, "resolve_tenant_domains", _resolver)

    # Baseline provider matches context → whole persist branch is skipped.
    app = OnboardingApp(_mistral_context())
    await app.persist_credentials("secret-key")
    assert resolver_calls == []


@pytest.mark.asyncio
async def test_persist_credentials_bubbles_partial_console_url_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the batch reports a per-field failure, the caller sees which
    field failed so the user can be told what to fix.
    """
    saved: list[ProviderCredentialsPersistRequest] = []

    def _spy_persist_api_key(*_args: Any, **kwargs: Any) -> str:
        return "completed"

    async def _spy_persist_provider_credentials(
        request: ProviderCredentialsPersistRequest,
    ) -> ProviderCredentialsPersistResult:
        saved.append(request)
        return ProviderCredentialsPersistResult(
            provider=True, console_base_url=False, vibe_base_url=None
        )

    monkeypatch.setattr(onboarding, "persist_api_key", _spy_persist_api_key)
    monkeypatch.setattr(
        onboarding, "persist_provider_credentials", _spy_persist_provider_credentials
    )

    app = OnboardingApp(_mistral_context())
    app.apply_custom_domain("https://console.acme.internal")
    result = await app.persist_credentials("secret-key")

    assert result == "provider_config_error:failed to persist console_base_url"
    assert len(saved) == 1


def test_mistral_default_domain_resets_api_base_and_vibe_url() -> None:
    """Switching back to Mistral must also reset api_base and vibe_base_url so
    a re-onboarding run after an on-prem session doesn't re-persist tenant URLs.
    """
    on_prem_provider = DEFAULT_PROVIDERS[0].model_copy(
        update={
            "api_base": "https://api.acme.internal/v1",
            "browser_auth_base_url": "https://console.acme.internal",
            "browser_auth_api_base_url": "https://console.acme.internal/api",
        }
    )
    app = OnboardingApp(OnboardingContext(provider=on_prem_provider))
    app.apply_mistral_default_domain()

    assert app._provider.api_base == f"{DEFAULT_MISTRAL_SERVER_URL}/v1"
    assert app._vibe_base_url == DEFAULT_VIBE_BASE_URL


@pytest.mark.asyncio
async def test_persist_credentials_console_drift_forces_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only console_base_url drifted (provider object is otherwise equal),
    persist_credentials must not short-circuit — it must write the corrected URL.
    """
    _persist_kwargs, saved_requests = _patch_persistence(monkeypatch)

    # Config has the wrong console URL; provider fields are otherwise at defaults.
    ctx = OnboardingContext(
        provider=DEFAULT_PROVIDERS[0],
        console_base_url="https://stale-console.example.com",
    )
    app = OnboardingApp(ctx)
    # apply_mistral_default_domain resets console URL to the Mistral default.
    app.apply_mistral_default_domain()
    result = await app.persist_credentials("secret-key")

    assert result == "completed"
    # provider objects are equal but console_base_url drifted — must not short-circuit.
    assert len(saved_requests) == 1
    # The corrected (Mistral default) console URL must have been submitted.
    assert saved_requests[0].console_base_url == "https://console.mistral.ai"
