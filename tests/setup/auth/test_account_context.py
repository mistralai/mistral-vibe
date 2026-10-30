from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tests.conftest import build_test_vibe_config
from vibe.app_server._account import AccountController
from vibe.core.paths import WHOAMI_CACHE_FILE
from vibe.setup.auth import _oidc_runtime
from vibe.setup.auth.whoami import (
    HttpAccountGateway,
    WhoAmICache,
    WhoAmIResult,
    load_cached_whoami,
    store_cached_whoami,
)
from vibe.utils.api_keys import resolve_api_key

# Producer: dashboard/dashboard/users/services/vibe_account_context.py:resolve_vibe_account_context.
_CONTEXT = {
    "organization_id": "org",
    "workspace_id": "workspace",
    "plan_type": "CHAT",
    "plan_name": "TEAM",
    "has_paid_vibe_entitlement": True,
    "organization_kind": "S",
    "customer_id": "customer",
    "api_base": "https://api.mistral.ai",
    "vibe_base": "https://chat.mistral.ai",
    "can_access_private_resources": True,
}


@pytest.mark.asyncio
async def test_oidc_session_reads_account_context(monkeypatch, tokens, respx_mock):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-api-key")
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "org")
    monkeypatch.setenv("VIBE_CLI_OIDC_WORKSPACE_ID", "workspace")
    whoami = respx_mock.get("https://console.mistral.ai/api/vibe/whoami")
    route = respx_mock.get("https://console.mistral.ai/api/vibe/account-context").mock(
        return_value=httpx.Response(200, json=_CONTEXT)
    )
    api_key = resolve_api_key("MISTRAL_API_KEY")
    assert api_key is not None
    result = await HttpAccountGateway().read(
        base_url="https://console.mistral.ai", api_key=api_key
    )
    assert result.plan_name == "TEAM"
    assert result.has_paid_vibe_entitlement
    assert result.can_access_private_resources
    headers = route.calls.last.request.headers
    assert headers["Authorization"] == f"Bearer {tokens.id_token.get_secret_value()}"
    assert headers["x-external-organization-id"] == "org"
    assert headers["x-external-workspace-id"] == "workspace"
    assert not whoami.called


@pytest.mark.asyncio
async def test_api_key_reads_whoami(monkeypatch, respx_mock):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "false")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-api-key")
    account_context = respx_mock.get(
        "https://console.mistral.ai/api/vibe/account-context"
    )
    # Producer: dashboard/dashboard/users/code/vibe_routes.py:whoami.
    route = respx_mock.get("https://console.mistral.ai/api/vibe/whoami").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_type": "API",
                "plan_name": "PAY_AS_YOU_GO",
                "prompt_switching_to_pro_plan": True,
                "organization_kind": "S",
                "customer_id": "customer",
                "api_base": "https://api.mistral.ai",
                "vibe_base": "https://chat.mistral.ai",
                "primitive_access_scope": "personal_and_shared",
            },
        )
    )
    api_key = resolve_api_key("MISTRAL_API_KEY")
    assert api_key == "test-api-key"
    result = await HttpAccountGateway().read(
        base_url="https://console.mistral.ai", api_key=api_key
    )
    assert result.plan_name == "PAY_AS_YOU_GO"
    assert result.prompt_switching_to_pro_plan
    assert not result.has_paid_vibe_entitlement
    assert result.can_access_private_resources
    headers = route.calls.last.request.headers
    assert headers["Authorization"] == "Bearer test-api-key"
    assert "x-external-organization-id" not in headers
    assert not account_context.called


@pytest.mark.asyncio
async def test_oidc_account_cache_does_not_reuse_another_tenant(
    monkeypatch, tokens, respx_mock
):
    monkeypatch.setattr(
        _oidc_runtime, "_resolve_id_token", lambda: tokens.id_token.get_secret_value()
    )
    route = respx_mock.get("https://console.mistral.ai/api/vibe/account-context").mock(
        return_value=httpx.Response(200, json=_CONTEXT)
    )
    cache = WhoAmICache()
    await cache.resolve(base_url="https://console.mistral.ai", api_key="vibe-cli-oidc")
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "other-org")
    route.mock(
        return_value=httpx.Response(
            200,
            json={
                **_CONTEXT,
                "organization_id": "other-org",
                "plan_name": "FREE",
                "has_paid_vibe_entitlement": False,
            },
        )
    )
    second = await cache.resolve(
        base_url="https://console.mistral.ai", api_key="vibe-cli-oidc"
    )
    assert second is not None
    assert second.plan_name == "FREE"
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_oidc_account_view_has_no_api_key_preview(monkeypatch, tokens):
    config = build_test_vibe_config()
    host = MagicMock(
        config=config,
        config_orchestrator=MagicMock(config=config),
        apply_account_whoami=AsyncMock(),
    )
    gateway = MagicMock(
        read=AsyncMock(return_value=WhoAmIResult.model_validate(_CONTEXT))
    )
    view = await AccountController(host, gateway).read()
    assert view.status.value == "ready"
    assert view.api_key is None
    assert view.plan is not None
    assert view.plan.name == "TEAM"
    assert view.plan_offer is None


@pytest.mark.asyncio
async def test_oidc_account_context_never_rewrites_tenant_domains(monkeypatch, tokens):
    """A dedicated api_base would fail every OIDC request (the registration
    allows only its own hosts) and persist across restarts.
    """
    from vibe.app_server import _account as account_module

    reconcile = AsyncMock()
    monkeypatch.setattr(account_module, "reconcile_tenant_domains", reconcile)
    config = build_test_vibe_config()
    host = MagicMock(
        config=config,
        config_orchestrator=MagicMock(config=config),
        apply_account_whoami=AsyncMock(),
    )
    dedicated = WhoAmIResult.model_validate({
        **_CONTEXT,
        "api_base": "https://api.tenant.example",
        "vibe_base": "https://chat.tenant.example",
    })
    gateway = MagicMock(read=AsyncMock(return_value=dedicated))
    view = await AccountController(host, gateway).read()
    assert view.status.value == "ready"
    reconcile.assert_not_awaited()


def test_account_context_cache_invalidates_legacy_format():
    import json

    store_cached_whoami("api-key", WhoAmIResult.model_validate(_CONTEXT))
    entries = json.loads(WHOAMI_CACHE_FILE.path.read_text())
    for entry in entries.values():
        entry["format"] = 2
    WHOAMI_CACHE_FILE.path.write_text(json.dumps(entries))
    assert load_cached_whoami("api-key") is None
