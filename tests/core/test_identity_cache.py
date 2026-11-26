from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.stubs.fake_identity_gateway import FakeIdentityGateway
from vibe.core.identity import IdentityResult
from vibe.core.identity_cache import IdentityCache
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialProvider,
    PlatformCredentialSnapshot,
)


@pytest.mark.asyncio
async def test_caches_success_by_opaque_credential_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = IdentityCache()
    gateway = FakeIdentityGateway(IdentityResult(id="user-1"))
    credentials = ApiKeyPlatformCredentialProvider("MISTRAL_API_KEY")
    monkeypatch.setenv("MISTRAL_API_KEY", "first-secret")

    first = await cache.resolve(
        base_url="https://api.mistral.ai/v1", credentials=credentials, gateway=gateway
    )
    cached = await cache.resolve(
        base_url="https://api.mistral.ai/v1/", credentials=credentials, gateway=gateway
    )
    monkeypatch.setenv("MISTRAL_API_KEY", "second-secret")
    rotated = await cache.resolve(
        base_url="https://api.mistral.ai/v1", credentials=credentials, gateway=gateway
    )

    assert first == cached == rotated == IdentityResult(id="user-1")
    assert gateway.calls == ["https://api.mistral.ai/v1", "https://api.mistral.ai/v1"]
    assert "first-secret" not in repr(cache.__dict__)
    assert "second-secret" not in repr(cache.__dict__)


@pytest.mark.asyncio
async def test_concurrent_cache_misses_share_one_identity_request() -> None:
    class _SlowGateway(FakeIdentityGateway):
        async def read(self, **kwargs):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0)
            return await super().read(**kwargs)

    cache = IdentityCache()
    gateway = _SlowGateway(IdentityResult(id="user-1"))
    credentials = ApiKeyPlatformCredentialProvider.from_api_key("secret")

    results = await asyncio.gather(
        *(
            cache.resolve(
                base_url="https://api.mistral.ai/v1",
                credentials=credentials,
                gateway=gateway,
            )
            for _ in range(3)
        )
    )

    assert results == [IdentityResult(id="user-1")] * 3
    assert gateway.calls == ["https://api.mistral.ai/v1"]


@pytest.mark.asyncio
async def test_concurrent_rejections_do_not_resend_the_rejected_credential() -> None:
    cache = IdentityCache()
    gateway = FakeIdentityGateway(unauthorized=True, reject_credentials=True)
    credentials = ApiKeyPlatformCredentialProvider.from_api_key("rejected-secret")

    results = await asyncio.gather(
        *(
            cache.resolve(
                base_url="https://api.mistral.ai/v1",
                credentials=credentials,
                gateway=gateway,
            )
            for _ in range(3)
        )
    )

    assert results == [None, None, None]
    assert gateway.calls == ["https://api.mistral.ai/v1"]


@pytest.mark.asyncio
async def test_cache_distinguishes_provider_namespaces_with_the_same_revision() -> None:
    cache = IdentityCache()
    first_credentials = AsyncMock(spec=PlatformCredentialProvider)
    first_credentials.resolve.return_value = PlatformCredentialSnapshot(
        kind="oauth_access_token",
        headers={"Authorization": "Bearer first-access-token"},
        cache_namespace="oauth:first-connection",
        revision="shared-revision",
    )
    second_credentials = AsyncMock(spec=PlatformCredentialProvider)
    second_credentials.resolve.return_value = PlatformCredentialSnapshot(
        kind="oauth_access_token",
        headers={"Authorization": "Bearer second-access-token"},
        cache_namespace="oauth:second-connection",
        revision="shared-revision",
    )

    first_identity = await cache.resolve(
        base_url="https://api.mistral.ai/v1",
        credentials=first_credentials,
        gateway=FakeIdentityGateway(IdentityResult(id="first-user")),
    )
    second_identity = await cache.resolve(
        base_url="https://api.mistral.ai/v1",
        credentials=second_credentials,
        gateway=FakeIdentityGateway(IdentityResult(id="second-user")),
    )

    assert first_identity == IdentityResult(id="first-user")
    assert second_identity == IdentityResult(id="second-user")


@pytest.mark.asyncio
async def test_failures_are_not_cached() -> None:
    cache = IdentityCache()
    gateway = FakeIdentityGateway(unauthorized=True)
    credentials = ApiKeyPlatformCredentialProvider.from_api_key("secret")

    first = await cache.resolve(
        base_url="https://api.mistral.ai/v1", credentials=credentials, gateway=gateway
    )
    second = await cache.resolve(
        base_url="https://api.mistral.ai/v1",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("secret"),
        gateway=gateway,
    )

    assert first is second is None
    assert gateway.calls == ["https://api.mistral.ai/v1", "https://api.mistral.ai/v1"]


@pytest.mark.asyncio
async def test_invalid_configured_platform_base_url_fails_open() -> None:
    # Producer contract: ProviderConfig.api_base is a plain string populated from
    # user-managed TOML by core/config/layers/_base.py::_read_toml_snapshot.
    gateway = FakeIdentityGateway(IdentityResult(id="user-1"))

    result = await IdentityCache().resolve(
        base_url="https://api.mistral.ai/v1?destination=other",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("secret"),
        gateway=gateway,
    )

    assert result is None
    assert gateway.calls == []
