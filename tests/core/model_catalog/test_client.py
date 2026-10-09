from __future__ import annotations

import httpx
import pytest
import respx

from vibe.core.model_catalog._constants import build_model_catalog_url
from vibe.core.model_catalog.client import ModelCatalogClient

_TEST_API_BASE = "https://api.mistral.ai/v1"
_TEST_URL = build_model_catalog_url(_TEST_API_BASE)
assert _TEST_URL is not None

_CATALOG_PAYLOAD = {
    "models": [
        {
            "id": "mistral-medium-3.5",
            "label": "Mistral Medium 3.5",
            "aliases": ["mistral-medium-2505"],
            "recommended": True,
            "max_context_length": 262144,
            "parameters": {"temperature": 0.3, "reasoning": ["off", "high"]},
            "capabilities": {
                "completion_chat": True,
                "function_calling": True,
                "vision": True,
            },
        }
    ]
}


def _make_client() -> ModelCatalogClient:
    return ModelCatalogClient(url=_TEST_URL)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_happy_path_sends_bearer_token() -> None:
    route = respx.get(_TEST_URL).mock(
        return_value=httpx.Response(200, json=_CATALOG_PAYLOAD)
    )
    client = _make_client()
    response = await client.fetch("test-key")
    await client.aclose()

    assert route.called
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-key"
    assert response is not None
    assert response.models[0].id == "mistral-medium-3.5"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_empty_catalog_is_a_success() -> None:
    respx.get(_TEST_URL).mock(return_value=httpx.Response(200, json={"models": []}))
    client = _make_client()
    response = await client.fetch("test-key")
    await client.aclose()

    assert response is not None
    assert response.models == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_http_error_returns_none() -> None:
    respx.get(_TEST_URL).mock(return_value=httpx.Response(503, text="unavailable"))
    client = _make_client()
    response = await client.fetch("test-key")
    await client.aclose()

    assert response is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_network_error_returns_none() -> None:
    respx.get(_TEST_URL).mock(side_effect=httpx.ConnectError("offline"))
    client = _make_client()
    response = await client.fetch("test-key")
    await client.aclose()

    assert response is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_non_json_body_returns_none() -> None:
    respx.get(_TEST_URL).mock(return_value=httpx.Response(200, text="<html>"))
    client = _make_client()
    response = await client.fetch("test-key")
    await client.aclose()

    assert response is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_non_catalog_payload_returns_none() -> None:
    respx.get(_TEST_URL).mock(return_value=httpx.Response(200, json={"error": True}))
    client = _make_client()
    response = await client.fetch("test-key")
    await client.aclose()

    assert response is None


@pytest.mark.asyncio
async def test_fetch_without_url_makes_no_request() -> None:
    client = ModelCatalogClient(url=build_model_catalog_url(""))

    assert client._url is None
    response = await client.fetch("test-key")
    await client.aclose()

    assert response is None
