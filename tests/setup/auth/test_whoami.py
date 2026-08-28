from __future__ import annotations

import json

import httpx
import pytest

from tests.stubs.fake_account_gateway import FakeAccountGateway
from vibe.app_server.models import AccountPlanKind
from vibe.core.config import DEFAULT_PROVIDERS, ProviderConfig
from vibe.core.paths import WHOAMI_CACHE_FILE
from vibe.setup.auth.whoami import (
    _WHOAMI_CACHE_TTL_SECONDS,
    AccountGatewayUnauthorized,
    AccountGatewayUnavailable,
    HttpAccountGateway,
    WhoAmICache,
    WhoAmIResult,
    fetch_whoami,
    load_cached_whoami,
    resolve_tenant_domains,
    store_cached_whoami,
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


def test_derive_user_plan_maps_known_plans() -> None:
    from vibe.setup.auth.whoami import derive_user_plan

    assert derive_user_plan(None) is None
    assert (
        derive_user_plan(
            WhoAmIResult(
                plan_type=AccountPlanKind.CHAT,
                plan_name="free",
                prompt_switching_to_pro_plan=False,
            )
        )
        == "Free"
    )
    assert (
        derive_user_plan(
            WhoAmIResult(
                plan_type=AccountPlanKind.CHAT,
                plan_name="individual",
                prompt_switching_to_pro_plan=False,
            )
        )
        == "Pro"
    )
    assert (
        derive_user_plan(
            WhoAmIResult(
                plan_type=AccountPlanKind.MISTRAL_CODE,
                plan_name="E",
                prompt_switching_to_pro_plan=False,
            )
        )
        == "Code Enterprise"
    )
    assert (
        derive_user_plan(
            WhoAmIResult(
                plan_type=AccountPlanKind.API,
                plan_name="free",
                prompt_switching_to_pro_plan=False,
            )
        )
        == "Free API"
    )
    assert (
        derive_user_plan(
            WhoAmIResult(
                plan_type=AccountPlanKind.API,
                plan_name="scale",
                prompt_switching_to_pro_plan=False,
            )
        )
        == "PAYG API"
    )


def test_derive_user_plan_returns_none_for_unknown_plan() -> None:
    from vibe.setup.auth.whoami import derive_user_plan

    assert (
        derive_user_plan(
            WhoAmIResult(
                plan_type=AccountPlanKind.CHAT,
                plan_name="unknown",
                prompt_switching_to_pro_plan=False,
            )
        )
        is None
    )


def test_store_and_load_cached_whoami_round_trips() -> None:
    result = WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name="TEAM")

    store_cached_whoami("secret", result)

    assert load_cached_whoami("secret") == result


def test_load_cached_whoami_returns_none_when_missing() -> None:
    assert load_cached_whoami("secret") is None


def test_load_cached_whoami_is_keyed_by_api_key() -> None:
    store_cached_whoami(
        "secret-one", WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name="TEAM")
    )

    assert load_cached_whoami("secret-two") is None


def test_load_cached_whoami_returns_none_when_stale() -> None:
    from vibe.setup.auth.whoami import _whoami_cache_key

    store_cached_whoami(
        "secret", WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name="TEAM")
    )

    path = WHOAMI_CACHE_FILE.path
    entries = json.loads(path.read_text())
    entries[_whoami_cache_key("secret")]["stored_at_timestamp"] -= (
        _WHOAMI_CACHE_TTL_SECONDS + 1
    )
    path.write_text(json.dumps(entries))

    assert load_cached_whoami("secret") is None


def test_load_cached_whoami_fails_open_on_corrupt_file() -> None:
    path = WHOAMI_CACHE_FILE.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid json")

    assert load_cached_whoami("secret") is None


@pytest.mark.asyncio
async def test_whoami_cache_resolve_reads_through_disk() -> None:
    # A brand-new cache instance (empty in-memory) must serve from the
    # cross-session on-disk cache without a second network fetch.
    result = WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name="INDIVIDUAL")
    warm_gateway = FakeAccountGateway(result)
    first = await WhoAmICache().resolve(
        base_url="https://console.test", api_key="secret", gateway=warm_gateway
    )
    assert first == result
    assert len(warm_gateway.calls) == 1

    cold_gateway = FakeAccountGateway(result)
    second = await WhoAmICache().resolve(
        base_url="https://console.test", api_key="secret", gateway=cold_gateway
    )
    assert second == result
    assert cold_gateway.calls == []


@pytest.mark.asyncio
async def test_whoami_cache_resolve_persists_to_disk() -> None:
    result = WhoAmIResult(plan_type=AccountPlanKind.API, plan_name="PAY_AS_YOU_GO")

    await WhoAmICache().resolve(
        base_url="https://console.test",
        api_key="secret",
        gateway=FakeAccountGateway(result),
    )

    assert load_cached_whoami("secret") == result


@pytest.mark.asyncio
async def test_whoami_cache_resolve_does_not_persist_failures() -> None:
    resolved = await WhoAmICache().resolve(
        base_url="https://console.test",
        api_key="secret",
        gateway=FakeAccountGateway(unavailable=True),
    )

    assert resolved is None
    assert load_cached_whoami("secret") is None
