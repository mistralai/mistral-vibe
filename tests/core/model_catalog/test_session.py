from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from vibe.core.model_catalog import (
    ModelCatalogResponse,
    fetch_and_cache_model_catalog,
    parse_model_catalog_response,
    refresh_model_catalog,
)
from vibe.core.model_catalog._constants import build_model_catalog_url
import vibe.core.model_catalog.session as session_module
import vibe.core.telemetry.send as telemetry_send

_TEST_API_BASE = "https://api.mistral.ai/v1"
_TEST_URL = build_model_catalog_url(_TEST_API_BASE)
assert _TEST_URL is not None

_CATALOG_PAYLOAD = {
    "models": [{"id": "mistral-medium-3.5", "label": "Mistral Medium 3.5"}]
}


def _config(*, flag: bool = True) -> Any:
    config = MagicMock()
    config.experimental_enable_model_catalog = flag
    return config


class _NoRequestClient:
    """Test double that fails the test if the fetch path is reached."""

    @classmethod
    def from_provider(cls, provider: Any) -> _NoRequestClient:
        return cls()

    async def fetch(self, api_key: str) -> Any:
        raise AssertionError("no request should be made")

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_refresh_is_gated_on_the_experimental_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_module, "ModelCatalogClient", _NoRequestClient)

    assert await refresh_model_catalog(_config(flag=False)) is None


@pytest.mark.asyncio
async def test_refresh_is_gated_on_a_mistral_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_module, "ModelCatalogClient", _NoRequestClient)
    monkeypatch.setattr(
        telemetry_send, "get_mistral_provider_and_api_key", lambda _config: None
    )

    assert await refresh_model_catalog(_config(flag=True)) is None


@pytest.mark.asyncio
@respx.mock
async def test_refresh_fetches_with_the_mistral_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = respx.get(_TEST_URL).mock(
        return_value=httpx.Response(200, json=_CATALOG_PAYLOAD)
    )
    provider = MagicMock(api_base=_TEST_API_BASE)
    monkeypatch.setattr(
        telemetry_send,
        "get_mistral_provider_and_api_key",
        lambda _config: (provider, "test-key"),
    )

    catalog = await refresh_model_catalog(_config(flag=True))

    assert route.called
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-key"
    assert catalog is not None
    assert catalog.models[0].id == "mistral-medium-3.5"


@pytest.mark.asyncio
@respx.mock
async def test_refresh_returns_none_when_the_endpoint_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    respx.get(_TEST_URL).mock(side_effect=httpx.ConnectError("offline"))
    provider = MagicMock(api_base=_TEST_API_BASE)
    monkeypatch.setattr(
        telemetry_send,
        "get_mistral_provider_and_api_key",
        lambda _config: (provider, "test-key"),
    )

    assert await refresh_model_catalog(_config(flag=True)) is None


@pytest.mark.asyncio
async def test_fetch_and_cache_stores_and_returns_the_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = parse_model_catalog_response({
        "models": [{"id": "mistral-medium-3.5", "label": "Mistral Medium 3.5"}]
    })
    assert catalog is not None
    monkeypatch.setattr(
        session_module, "refresh_model_catalog", AsyncMock(return_value=catalog)
    )
    stored: list[ModelCatalogResponse] = []
    monkeypatch.setattr(
        session_module,
        "store_cached_model_catalog",
        lambda _config, response: stored.append(response),
    )

    result = await fetch_and_cache_model_catalog(_config(flag=True))

    assert result is catalog
    assert stored == [catalog]


@pytest.mark.asyncio
async def test_fetch_and_cache_never_raises_on_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(_config: Any) -> ModelCatalogResponse:
        raise RuntimeError("ssl context blew up")

    monkeypatch.setattr(session_module, "refresh_model_catalog", _boom)
    monkeypatch.setattr(session_module, "store_cached_model_catalog", lambda *_: None)

    assert await fetch_and_cache_model_catalog(_config(flag=True)) is None


@pytest.mark.asyncio
async def test_fetch_and_cache_returns_the_catalog_even_when_storing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = parse_model_catalog_response({"models": []})
    assert catalog is not None
    monkeypatch.setattr(
        session_module, "refresh_model_catalog", AsyncMock(return_value=catalog)
    )

    def _explode(_config: Any, _response: ModelCatalogResponse) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(session_module, "store_cached_model_catalog", _explode)

    assert await fetch_and_cache_model_catalog(_config(flag=True)) is catalog
