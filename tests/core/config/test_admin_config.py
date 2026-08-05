from __future__ import annotations

import httpx
import pytest
import respx

from vibe.core.config.admin_config import (
    MANAGED_CONFIG_PATH,
    ManagedConfig,
    fetch_managed_config,
)

_URL = f"http://test{MANAGED_CONFIG_PATH}"


@pytest.mark.asyncio
async def test_returns_enabled_config(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(_URL).mock(
        return_value=httpx.Response(
            200, json={"state": "enabled", "toml": 'active_model = "x"'}
        )
    )

    result = await fetch_managed_config("http://test", "api-key")

    assert route.called
    assert route.calls.last.request.headers["Authorization"] == "Bearer api-key"
    assert result.error is None
    assert result.config is not None
    assert result.config.is_enabled is True
    assert result.config.toml == 'active_model = "x"'


@pytest.mark.asyncio
async def test_disabled_state_is_not_enabled(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(_URL).mock(
        return_value=httpx.Response(200, json={"state": "disabled"})
    )

    result = await fetch_managed_config("http://test", "api-key")

    assert result.error is None
    assert result.config is not None
    assert result.config.is_enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 500])
async def test_error_status_reports_error(
    respx_mock: respx.MockRouter, status_code: int
) -> None:
    respx_mock.get(_URL).mock(return_value=httpx.Response(status_code))

    result = await fetch_managed_config("http://test", "api-key")

    assert result.config is None
    assert result.error == f"HTTP {status_code}"


@pytest.mark.asyncio
async def test_network_error_reports_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(_URL).mock(side_effect=httpx.ConnectError("boom"))

    result = await fetch_managed_config("http://test", "api-key")

    assert result.config is None
    assert result.error is not None


@pytest.mark.asyncio
async def test_bad_json_reports_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(_URL).mock(return_value=httpx.Response(200, text="not json"))

    result = await fetch_managed_config("http://test", "api-key")

    assert result.config is None
    assert result.error is not None


def test_is_enabled_requires_toml() -> None:
    assert ManagedConfig(state="enabled", toml=None).is_enabled is False
    assert ManagedConfig(state="enabled", toml="").is_enabled is False
