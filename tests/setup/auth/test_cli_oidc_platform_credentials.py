from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from pydantic import SecretStr
import pytest

from vibe.core.platform_auth import (
    PlatformCredentialRequired,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)
from vibe.setup.auth._cli_oidc_platform_credentials import (
    CliOidcPlatformCredentialProvider,
)
from vibe.setup.auth._oidc_config import OidcError
from vibe.setup.auth._oidc_credentials import OidcTokens
from vibe.setup.auth._oidc_tenant import OidcTenant, OidcTenantResolver, TenantSelection

API = PlatformCredentialTarget(base_url="https://api.mistral.ai/v1")


def _provider(
    tokens: OidcTokens, discover: AsyncMock | None = None
) -> tuple[CliOidcPlatformCredentialProvider, list[OidcTokens]]:
    current = [tokens]
    resolver = OidcTenantResolver(
        discover or AsyncMock(side_effect=AssertionError("discovery"))
    )
    provider = CliOidcPlatformCredentialProvider(
        resolver,
        id_token=lambda: current[0].id_token.get_secret_value(),
        stored_tokens=lambda: current[0],
    )
    return provider, current


@pytest.mark.asyncio
async def test_resolves_the_id_token_with_tenant_headers(tokens):
    provider, _ = _provider(tokens)
    resolved = await provider.resolve(API)
    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.kind == "oidc_id_token"
    assert resolved.headers == {
        "Authorization": f"Bearer {tokens.id_token.get_secret_value()}",
        "x-external-organization-id": "test-org",
    }
    assert resolved.follows_redirects is False
    assert resolved.source == "the CLI OIDC session"
    assert tokens.id_token.get_secret_value() not in repr(resolved)
    assert resolved.principal is not None
    for private in ("user", "test-org", tokens.id_token.get_secret_value()):
        assert private not in resolved.principal


@pytest.mark.asyncio
async def test_principal_survives_token_rotation_but_not_a_user_change(tokens):
    provider, current = _provider(tokens)
    first = await provider.resolve(API)
    current[0] = tokens.model_copy(update={"id_token": SecretStr("rotated-id")})
    rotated = await provider.resolve(API)
    current[0] = tokens.model_copy(update={"subject": "other-user"})
    other = await provider.resolve(API)
    assert isinstance(first, PlatformCredentialSnapshot)
    assert isinstance(rotated, PlatformCredentialSnapshot)
    assert isinstance(other, PlatformCredentialSnapshot)
    assert rotated.revision != first.revision
    assert rotated.cache_key == first.cache_key
    assert other.cache_key != first.cache_key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/v1",
        "http://api.mistral.ai/v1",
        "https://api.mistral.ai:8443/v1",
    ],
)
async def test_untrusted_destination_never_reads_a_token(tokens, url):
    def unreachable() -> str:
        raise AssertionError("token read")

    provider = CliOidcPlatformCredentialProvider(
        OidcTenantResolver(), id_token=unreachable, stored_tokens=lambda: tokens
    )
    result = await provider.resolve(PlatformCredentialTarget(base_url=url))
    assert result == PlatformCredentialRequired(reason="invalid")


@pytest.mark.asyncio
async def test_web_origins_and_websocket_audience_are_trusted(tokens):
    provider, _ = _provider(tokens)
    for url in ("https://chat.mistral.ai", "https://console.mistral.ai/api"):
        target = PlatformCredentialTarget(base_url=url)
        assert isinstance(await provider.resolve(target), PlatformCredentialSnapshot)


@pytest.mark.asyncio
async def test_discovers_the_tenant_once_for_concurrent_requests(monkeypatch, tokens):
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID")

    async def slow(*_: str) -> OidcTenant:
        await asyncio.sleep(0.01)
        return OidcTenant("org-a", "workspace-a")

    discover = AsyncMock(side_effect=slow)
    provider, current = _provider(tokens, discover)
    results = await asyncio.gather(*(provider.resolve(API) for _ in range(4)))
    assert all(isinstance(result, PlatformCredentialSnapshot) for result in results)
    first = results[0]
    assert isinstance(first, PlatformCredentialSnapshot)
    assert first.headers["x-external-organization-id"] == "org-a"
    assert first.headers["x-external-workspace-id"] == "workspace-a"
    current[0] = tokens.model_copy(update={"id_token": SecretStr("rotated-id")})
    assert isinstance(await provider.resolve(API), PlatformCredentialSnapshot)
    discover.assert_awaited_once()
    assert discover.await_args is not None
    assert discover.await_args.args == (
        "https://api.mistral.ai",
        tokens.id_token.get_secret_value(),
        "",
    )


@pytest.mark.asyncio
async def test_discovery_failure_requires_sign_in_without_leaking(monkeypatch, tokens):
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID")
    discover = AsyncMock(side_effect=RuntimeError(tokens.id_token.get_secret_value()))
    provider, _ = _provider(tokens, discover)
    assert await provider.resolve(API) == PlatformCredentialRequired(
        reason="reauthentication_required"
    )
    resolver = OidcTenantResolver(discover)
    with pytest.raises(OidcError) as caught:
        await resolver.resolve(
            "https://api.mistral.ai",
            "user",
            tokens.id_token.get_secret_value(),
            TenantSelection(),
        )
    assert tokens.id_token.get_secret_value() not in str(caught.value)


@pytest.mark.asyncio
async def test_refresh_failure_requires_sign_in(tokens):
    def refused() -> str:
        raise OidcError("OIDC re-login required: refresh token rejected")

    provider = CliOidcPlatformCredentialProvider(
        OidcTenantResolver(), id_token=refused, stored_tokens=lambda: tokens
    )
    assert await provider.resolve(API) == PlatformCredentialRequired(
        reason="reauthentication_required"
    )


@pytest.mark.asyncio
async def test_a_login_between_reads_is_transient(tokens):
    provider = CliOidcPlatformCredentialProvider(
        OidcTenantResolver(), id_token=lambda: "old-id", stored_tokens=lambda: tokens
    )
    assert await provider.resolve(API) == PlatformCredentialRequired(reason="transient")


@pytest.mark.asyncio
@pytest.mark.parametrize("rotated", [False, True])
async def test_rejection_compares_the_sent_token(tokens, rotated):
    provider, current = _provider(tokens)
    sent = await provider.resolve(API)
    assert isinstance(sent, PlatformCredentialSnapshot)
    if rotated:
        current[0] = tokens.model_copy(update={"id_token": SecretStr("rotated-id")})
    result = await provider.reject(
        API, observed=sent.identity, reason="http_unauthorized"
    )
    if rotated:
        assert isinstance(result, PlatformCredentialSnapshot)
        assert result.revision != sent.revision
        return
    assert result == PlatformCredentialRequired(
        reason="rejected", observed_revision=sent.revision
    )
    assert await provider.resolve(API) == result
    current[0] = tokens.model_copy(update={"id_token": SecretStr("rotated-id")})
    assert isinstance(await provider.resolve(API), PlatformCredentialSnapshot)


def test_cached_principal_never_discovers(monkeypatch, tokens):
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID")
    provider, _ = _provider(tokens)
    assert provider.cached_principal() is None
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "test-org")
    assert provider.cached_principal() is not None
