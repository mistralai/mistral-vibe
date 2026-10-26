from __future__ import annotations

import pytest

from tests.stubs.fake_identity_gateway import FakeIdentityGateway
from vibe.core.identity import IdentityResult, fetch_identity
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialProvider,
)


@pytest.mark.asyncio
async def test_fetch_identity_returns_result_on_success() -> None:
    result = IdentityResult.model_validate({
        "id": "user-1",
        "organization": {"id": "org-123", "name": "Acme"},
        "workspace": {"id": "ws-1", "name": "Default"},
    })
    gateway = FakeIdentityGateway(result=result)

    got = await fetch_identity(
        base_url="https://api.mistral.ai/v1",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("secret"),
        gateway=gateway,
    )

    assert got is result
    assert gateway.calls == ["https://api.mistral.ai/v1"]


@pytest.mark.asyncio
async def test_fetch_identity_returns_none_on_unauthorized() -> None:
    gateway = FakeIdentityGateway(unauthorized=True)

    got = await fetch_identity(
        base_url="https://api.mistral.ai/v1",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("secret"),
        gateway=gateway,
    )

    assert got is None


@pytest.mark.asyncio
async def test_fetch_identity_returns_none_on_unavailable() -> None:
    gateway = FakeIdentityGateway(unavailable=True)

    got = await fetch_identity(
        base_url="https://api.mistral.ai/v1",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("secret"),
        gateway=gateway,
    )

    assert got is None


@pytest.mark.asyncio
async def test_fetch_identity_returns_none_when_credentials_are_unavailable() -> None:
    got = await fetch_identity(
        base_url="https://api.mistral.ai/v1",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key(""),
    )

    assert got is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.mistral.ai/v1?destination=other",
        "https://user:password@api.mistral.ai/v1",
    ],
)
async def test_fetch_identity_returns_none_for_invalid_platform_base_url(
    base_url: str,
) -> None:
    got = await fetch_identity(
        base_url=base_url,
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("secret"),
    )

    assert got is None


@pytest.mark.asyncio
async def test_fetch_identity_forwards_timeout_to_gateway() -> None:
    class _RecordingGateway:
        def __init__(self) -> None:
            self.timeout: float | None | str = "unset"

        async def read(
            self,
            *,
            base_url: str,
            credentials: PlatformCredentialProvider,
            timeout: float | None = None,
        ) -> IdentityResult:
            del base_url, credentials
            self.timeout = timeout
            return IdentityResult(id="user-1")

    gateway = _RecordingGateway()
    got = await fetch_identity(
        base_url="https://api.mistral.ai/v1",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("secret"),
        gateway=gateway,
        timeout=2.0,
    )

    assert got is not None
    assert gateway.timeout == 2.0
