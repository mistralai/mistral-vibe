from __future__ import annotations

import httpx
import pytest

from vibe.app_server.models import AccountPlanKind
from vibe.core.config import DEFAULT_PROVIDERS, ProviderConfig
from vibe.setup.auth.whoami import (
    AccountGatewayUnauthorized,
    AccountGatewayUnavailable,
    HttpAccountGateway,
    WhoAmIResult,
    fetch_whoami,
    resolve_tenant_domains,
)


def _mistral_provider() -> ProviderConfig:
    return DEFAULT_PROVIDERS[0]


@pytest.mark.asyncio
async def test_fetch_whoami_returns_result_on_success(respx_mock) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_type": "API",
                "plan_name": "FREE",
                "api_base": "https://api.acme",
                "vibe_base": "https://chat.acme",
            },
        )
    )

    result = await fetch_whoami("https://console.test", "secret")

    assert result == WhoAmIResult(
        plan_type=AccountPlanKind.API,
        plan_name="FREE",
        api_base="https://api.acme",
        vibe_base="https://chat.acme",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_fetch_whoami_returns_none_on_unauthorized(
    respx_mock, status_code: int
) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(status_code)
    )

    assert await fetch_whoami("https://console.test", "bad") is None


@pytest.mark.asyncio
async def test_fetch_whoami_returns_none_on_server_error(respx_mock) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(500)
    )

    assert await fetch_whoami("https://console.test", "secret") is None


@pytest.mark.asyncio
async def test_fetch_whoami_returns_none_on_invalid_json(respx_mock) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(200, text="not-json")
    )

    assert await fetch_whoami("https://console.test", "secret") is None


@pytest.mark.asyncio
async def test_fetch_whoami_returns_none_on_network_error(respx_mock) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        side_effect=httpx.ConnectError("boom")
    )

    assert await fetch_whoami("https://console.test", "secret") is None


@pytest.mark.asyncio
async def test_http_account_gateway_maps_network_error(respx_mock) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        side_effect=httpx.ConnectError("boom")
    )

    with pytest.raises(AccountGatewayUnavailable):
        await HttpAccountGateway().read(
            base_url="https://console.test", api_key="secret"
        )


@pytest.mark.asyncio
async def test_http_account_gateway_raises_unauthorized_for_401(respx_mock) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(401)
    )

    with pytest.raises(AccountGatewayUnauthorized):
        await HttpAccountGateway().read(
            base_url="https://console.test", api_key="secret"
        )


@pytest.mark.asyncio
async def test_resolve_tenant_domains_returns_inputs_when_whoami_unreachable(
    respx_mock,
) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(500)
    )
    provider = _mistral_provider()

    new_provider, new_vibe = await resolve_tenant_domains(
        provider, "https://console.test", "secret", "https://vibe.old"
    )

    assert new_provider is provider
    assert new_vibe == "https://vibe.old"


@pytest.mark.asyncio
async def test_resolve_tenant_domains_returns_inputs_when_domains_missing(
    respx_mock,
) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(200, json={"plan_type": "API", "plan_name": "FREE"})
    )
    provider = _mistral_provider()

    new_provider, new_vibe = await resolve_tenant_domains(
        provider, "https://console.test", "secret", "https://vibe.old"
    )

    assert new_provider is provider
    assert new_vibe == "https://vibe.old"


@pytest.mark.asyncio
async def test_resolve_tenant_domains_applies_api_and_chat(respx_mock) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_type": "API",
                "plan_name": "FREE",
                "api_base": "https://api.tenant.corp/",
                "vibe_base": "https://chat.tenant.corp/",
            },
        )
    )
    provider = _mistral_provider()

    new_provider, new_vibe = await resolve_tenant_domains(
        provider, "https://console.test", "secret", "https://vibe.old"
    )

    assert new_provider.api_base == "https://api.tenant.corp/v1"
    assert new_vibe == "https://chat.tenant.corp"


@pytest.mark.asyncio
async def test_resolve_tenant_domains_ignores_only_chat_when_api_missing(
    respx_mock,
) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_type": "API",
                "plan_name": "FREE",
                "vibe_base": "https://chat.tenant.corp",
            },
        )
    )
    provider = _mistral_provider()

    new_provider, new_vibe = await resolve_tenant_domains(
        provider, "https://console.test", "secret", "https://vibe.old"
    )

    assert new_provider is provider  # untouched
    assert new_vibe == "https://chat.tenant.corp"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_url",
    [
        "http://api.tenant.corp",  # non-https
        "ftp://api.tenant.corp",  # wrong scheme
        "https://good.corp/../evil.corp",  # path traversal
        "not-a-url",  # missing scheme + host
        "https://",  # empty netloc
    ],
)
async def test_resolve_tenant_domains_rejects_hostile_api_url(
    respx_mock, bad_url: str
) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_type": "API",
                "plan_name": "FREE",
                "api_base": bad_url,
                "vibe_base": "https://chat.tenant.corp",
            },
        )
    )
    provider = _mistral_provider()

    new_provider, new_vibe = await resolve_tenant_domains(
        provider, "https://console.test", "secret", "https://vibe.old"
    )

    # api_base was rejected → provider unchanged, but valid vibe_base still applies
    assert new_provider.api_base == provider.api_base
    assert new_vibe == "https://chat.tenant.corp"


@pytest.mark.asyncio
async def test_resolve_tenant_domains_rejects_hostile_chat_url(respx_mock) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_type": "API",
                "plan_name": "FREE",
                "api_base": "https://api.tenant.corp",
                "vibe_base": "http://chat.tenant.corp",  # non-https
            },
        )
    )
    provider = _mistral_provider()

    new_provider, new_vibe = await resolve_tenant_domains(
        provider, "https://console.test", "secret", "https://vibe.old"
    )

    assert new_provider.api_base == "https://api.tenant.corp/v1"
    assert new_vibe == "https://vibe.old"  # vibe_base rejected → unchanged
