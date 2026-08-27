from __future__ import annotations

import pytest

from tests.stubs.fake_identity_gateway import FakeIdentityGateway
from vibe.core.identity import IdentityResult, fetch_identity


@pytest.mark.asyncio
async def test_fetch_identity_returns_result_on_success() -> None:
    result = IdentityResult.model_validate({
        "id": "user-1",
        "organization": {"id": "org-123", "name": "Acme"},
        "workspace": {"id": "ws-1", "name": "Default"},
    })
    gateway = FakeIdentityGateway(result=result)

    got = await fetch_identity(
        base_url="https://api.mistral.ai/v1", api_key="secret", gateway=gateway
    )

    assert got is result
    assert gateway.calls == [("https://api.mistral.ai/v1", "secret")]


@pytest.mark.asyncio
async def test_fetch_identity_returns_none_on_unauthorized() -> None:
    gateway = FakeIdentityGateway(unauthorized=True)

    got = await fetch_identity(
        base_url="https://api.mistral.ai/v1", api_key="secret", gateway=gateway
    )

    assert got is None


@pytest.mark.asyncio
async def test_fetch_identity_returns_none_on_unavailable() -> None:
    gateway = FakeIdentityGateway(unavailable=True)

    got = await fetch_identity(
        base_url="https://api.mistral.ai/v1", api_key="secret", gateway=gateway
    )

    assert got is None


@pytest.mark.asyncio
async def test_fetch_identity_forwards_timeout_to_gateway() -> None:
    class _RecordingGateway:
        def __init__(self) -> None:
            self.timeout: float | None | str = "unset"

        async def read(
            self, *, base_url: str, api_key: str, timeout: float | None = None
        ) -> IdentityResult:
            self.timeout = timeout
            return IdentityResult(id="user-1")

    gateway = _RecordingGateway()
    got = await fetch_identity(
        base_url="https://api.mistral.ai/v1",
        api_key="secret",
        gateway=gateway,
        timeout=2.0,
    )

    assert got is not None
    assert gateway.timeout == 2.0
