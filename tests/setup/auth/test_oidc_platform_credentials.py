from __future__ import annotations

from datetime import UTC, datetime
import time
from unittest.mock import AsyncMock

import httpx2
from pydantic import SecretStr
import pytest

from vibe.core.platform_auth import (
    MistralPlatformHTTPClient,
    PlatformCredentialRequired,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
    PlatformCredentialUnavailable,
    UntrustedPlatformDestination,
)
from vibe.setup.auth._oidc_config import OidcConfig, OidcError
from vibe.setup.auth._oidc_credentials import OidcTokens
from vibe.setup.auth._oidc_platform_credentials import OidcPlatformCredentialProvider


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    token = OidcTokens(
        issuer="https://issuer.example",
        client_id="cli",
        audience="https://api.example",
        subject="subject",
        access_token=SecretStr("access-token"),
        refresh_token=SecretStr("refresh-token"),
        id_token=SecretStr("id-token"),
        expires_at=time.time() + 3600,
    )
    resolver = AsyncMock(return_value=token)
    target = PlatformCredentialTarget(
        base_url="https://issuer.example/userinfo", audience=token.audience
    )
    config = OidcConfig(
        token.issuer, token.client_id, token.audience, "http://127.0.0.1:8766/callback"
    )
    return (
        OidcPlatformCredentialProvider(target, resolver, config=config),
        target,
        resolver,
        token,
    )


@pytest.mark.asyncio
async def test_provider_resolves_cli_access_tokens_using_existing_contract(credentials):
    provider, target, resolver, token = credentials
    resolved = await provider.resolve(target)
    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.kind == "oauth_access_token"
    assert resolved.headers == {"Authorization": "Bearer access-token"}
    assert resolved.expires_at == datetime.fromtimestamp(token.expires_at, UTC)
    assert provider.subject == token.subject
    assert "access-token" not in repr(resolved)
    resolver.assert_awaited_once()


@pytest.mark.asyncio
async def test_target_mismatch_never_resolves_a_token(credentials):
    provider, _, resolver, _ = credentials
    result = await provider.resolve(
        PlatformCredentialTarget(base_url="https://other.example")
    )
    assert result == PlatformCredentialRequired(reason="invalid")
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_audience_mismatch_fails_closed(credentials):
    provider, target, resolver, token = credentials
    resolver.return_value = token.model_copy(update={"audience": "other"})
    assert await provider.resolve(target) == PlatformCredentialRequired(
        reason="invalid"
    )


@pytest.mark.asyncio
async def test_revoked_revision_not_reused_but_refreshed_revision_can_be(credentials):
    provider, target, resolver, token = credentials
    first = await provider.resolve(target)
    assert isinstance(first, PlatformCredentialSnapshot)
    await provider.reject(target, observed=first.identity, reason="http_unauthorized")
    blocked = await provider.resolve(target)
    assert isinstance(blocked, PlatformCredentialRequired)
    assert blocked.reason == "rejected"
    resolver.return_value = token.model_copy(
        update={"access_token": SecretStr("replacement")}
    )
    replacement = await provider.resolve(target)
    assert isinstance(replacement, PlatformCredentialSnapshot)
    assert replacement.revision != first.revision


@pytest.mark.asyncio
async def test_http_client_attaches_only_access_token_and_does_not_replay_401(
    credentials, monkeypatch
):
    provider, target, resolver, _ = credentials
    monkeypatch.setenv("MISTRAL_API_KEY", "ambient-api-key")
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer access-token"
        return httpx2.Response(401)

    async with MistralPlatformHTTPClient(
        base_url=target.base_url,
        audience=target.audience,
        credentials=provider,
        transport=httpx2.MockTransport(handler),
    ) as client:
        assert (await client.get("https://issuer.example/userinfo")).status_code == 401
        with pytest.raises(PlatformCredentialUnavailable):
            await client.get("https://issuer.example/userinfo")
        with pytest.raises(UntrustedPlatformDestination):
            await client.get("https://untrusted.example/userinfo")
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_flag_off_never_calls_token_resolver(credentials, monkeypatch):
    provider, target, resolver, _ = credentials
    monkeypatch.delenv("VIBE_CLI_OIDC_ENABLED")
    with pytest.raises(OidcError, match="disabled"):
        await provider.resolve(target)
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_errors_are_redacted_and_relogin_stays_explicit(credentials):
    provider, target, resolver, _ = credentials
    resolver.side_effect = ValueError("refresh-token")
    assert await provider.resolve(target) == PlatformCredentialRequired(
        reason="transient"
    )
    resolver.side_effect = OidcError("OIDC re-login required: refresh token rejected")
    with pytest.raises(OidcError, match="re-login required"):
        await provider.resolve(target)
