from __future__ import annotations

import asyncio

import httpx2
import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    MistralPlatformHTTPClient,
)
from vibe.setup.auth._oidc_config import OidcConfig, OidcError
from vibe.setup.auth._oidc_credentials import OidcCredentialStore, OidcTenant
from vibe.setup.auth._oidc_runtime import oidc_headers
from vibe.utils.http import VibeAsyncHTTPClient


@pytest.fixture
def stored(monkeypatch, tokens):
    """The credential slot, holding what discovery saves with the session."""
    slot = [tokens]
    monkeypatch.setattr(OidcCredentialStore, "_load", lambda self: slot[0])
    monkeypatch.setattr(
        OidcCredentialStore, "_save", lambda self, value: slot.__setitem__(0, value)
    )
    return slot


@pytest.fixture
def discovery(monkeypatch, tokens, stored):
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID")
    requests = []
    original = VibeAsyncHTTPClient.__init__

    def reply(request):
        requests.append(request)
        if request.url.path == "/v1/users/me/workspaces":
            assert "x-external-organization-id" not in request.headers
            assert (
                request.headers["Authorization"]
                == f"Bearer {tokens.id_token.get_secret_value()}"
            )
            # Producer: dashboard/dashboard/users/public/routes.py:list_workspaces.
            return httpx2.Response(
                200,
                json={
                    "workspaces": [
                        {
                            "id": "workspace-a",
                            "organization_id": "org-a",
                            "name": "Default",
                        },
                        {
                            "id": "workspace-b",
                            "organization_id": "org-b",
                            "name": "Team",
                        },
                    ]
                },
            )
        return httpx2.Response(200, json={"ok": True})

    def init(self, **kwargs):
        kwargs["transport"] = httpx2.MockTransport(reply)
        original(self, **kwargs)

    monkeypatch.setattr(VibeAsyncHTTPClient, "__init__", init)
    return requests


@pytest.mark.asyncio
async def test_flag_alone_matches_production_defaults_and_discovers_tenant(
    tokens, discovery
):
    config = OidcConfig.from_env()
    provider = build_test_vibe_config().get_active_provider()
    assert provider.api_base == f"{config.audience}/v1"
    async with MistralPlatformHTTPClient(
        base_url=provider.api_base,
        credentials=ApiKeyPlatformCredentialProvider("MISTRAL_API_KEY"),
    ) as client:
        await client.get("users/me")
    request = discovery[-1]
    assert request.url.path == "/v1/users/me"
    assert (
        request.headers["Authorization"]
        == f"Bearer {tokens.id_token.get_secret_value()}"
    )
    assert request.headers["x-external-organization-id"] == "org-a"
    assert request.headers["x-external-workspace-id"] == "workspace-a"


@pytest.mark.asyncio
async def test_concurrent_requests_discover_workspace_once(tokens, discovery):
    results = await asyncio.gather(
        *(oidc_headers("https://api.mistral.ai") for _ in range(4))
    )
    assert all(value["x-external-organization-id"] == "org-a" for value in results)
    assert len(discovery) == 1


@pytest.mark.asyncio
async def test_discovered_tenant_is_stored_with_the_session(tokens, discovery, stored):
    await oidc_headers("https://api.mistral.ai")
    assert stored[0].tenant == OidcTenant(
        selection="", organization_id="org-a", workspace_id="workspace-a"
    )
    # Later requests read it from the stored session: no second discovery.
    await oidc_headers("https://api.mistral.ai")
    assert len(discovery) == 1


@pytest.mark.asyncio
async def test_workspace_override_resolves_its_organization(
    monkeypatch, tokens, discovery
):
    monkeypatch.setenv("VIBE_CLI_OIDC_WORKSPACE_ID", "workspace-b")
    headers = await oidc_headers("https://api.mistral.ai")
    assert headers["x-external-organization-id"] == "org-b"
    assert headers["x-external-workspace-id"] == "workspace-b"


@pytest.mark.asyncio
async def test_explicit_organization_bypasses_discovery(monkeypatch, tokens, discovery):
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "explicit-org")
    headers = await oidc_headers("https://api.mistral.ai")
    assert headers["x-external-organization-id"] == "explicit-org"
    assert discovery == []


@pytest.mark.asyncio
async def test_missing_selected_workspace_does_not_choose_another(
    monkeypatch, tokens, discovery
):
    monkeypatch.setenv("VIBE_CLI_OIDC_WORKSPACE_ID", "missing-workspace")
    with pytest.raises(OidcError, match="No accessible"):
        await oidc_headers("https://api.mistral.ai")


@pytest.mark.asyncio
async def test_empty_workspace_list_does_not_send_tenant_request(
    monkeypatch, tokens, discovery, stored
):
    original = VibeAsyncHTTPClient.__init__

    def init(self, **kwargs):
        original(self, **kwargs)
        self._transport = httpx2.MockTransport(
            lambda request: httpx2.Response(200, json={"workspaces": []})
        )

    monkeypatch.setattr(VibeAsyncHTTPClient, "__init__", init)
    with pytest.raises(OidcError, match="No accessible"):
        await oidc_headers("https://api.mistral.ai")
    assert stored[0].tenant is None


@pytest.mark.asyncio
async def test_workspace_override_searches_next_page(monkeypatch, tokens, discovery):
    monkeypatch.setenv("VIBE_CLI_OIDC_WORKSPACE_ID", "chosen")
    original = VibeAsyncHTTPClient.__init__
    offsets = []

    def reply(request):
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        if offset == 0:
            workspaces = [
                {"id": f"other-{index}", "organization_id": "other-org"}
                for index in range(100)
            ]
        else:
            workspaces = [{"id": "chosen", "organization_id": "chosen-org"}]
        return httpx2.Response(200, json={"workspaces": workspaces})

    def init(self, **kwargs):
        original(self, **kwargs)
        self._transport = httpx2.MockTransport(reply)

    monkeypatch.setattr(VibeAsyncHTTPClient, "__init__", init)
    headers = await oidc_headers("https://api.mistral.ai")
    assert headers["x-external-workspace-id"] == "chosen"
    assert headers["x-external-organization-id"] == "chosen-org"
    assert offsets == [0, 100]


@pytest.mark.asyncio
async def test_discovery_failure_is_safe_and_not_cached(
    monkeypatch, tokens, discovery, stored
):
    original = VibeAsyncHTTPClient.__init__

    def init(self, **kwargs):
        original(self, **kwargs)
        self._transport = httpx2.MockTransport(
            lambda request: httpx2.Response(401, json={"secret": "token-secret"})
        )

    monkeypatch.setattr(VibeAsyncHTTPClient, "__init__", init)
    with pytest.raises(OidcError) as caught:
        await oidc_headers("https://api.mistral.ai")
    assert "token-secret" not in str(caught.value)
    assert stored[0].tenant is None
