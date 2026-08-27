from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from vibe.core.config.admin_config import (
    MANAGED_CONFIG_PATH,
    MANAGED_CONFIG_RETRY_TRIES,
    ManagedConfig,
    fetch_managed_config,
)

_URL = f"http://test{MANAGED_CONFIG_PATH}"


@pytest.fixture
def no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("vibe.core.utils.retry.asyncio.sleep", AsyncMock())


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
@pytest.mark.parametrize("status_code", [401, 403, 404])
async def test_non_retryable_status_reports_error(
    respx_mock: respx.MockRouter, status_code: int
) -> None:
    route = respx_mock.get(_URL).mock(return_value=httpx.Response(status_code))

    result = await fetch_managed_config("http://test", "api-key")

    assert result.config is None
    assert result.error == f"HTTP {status_code}"
    assert route.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [500, 502, 503, 504, 429])
async def test_retryable_status_retries_then_reports_error(
    respx_mock: respx.MockRouter, status_code: int, no_retry_sleep: None
) -> None:
    route = respx_mock.get(_URL).mock(return_value=httpx.Response(status_code))

    result = await fetch_managed_config("http://test", "api-key")

    assert result.config is None
    assert result.error == f"HTTP {status_code}"
    assert route.call_count == MANAGED_CONFIG_RETRY_TRIES


@pytest.mark.asyncio
async def test_retryable_status_succeeds_on_retry(
    respx_mock: respx.MockRouter, no_retry_sleep: None
) -> None:
    route = respx_mock.get(_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(
                200, json={"state": "enabled", "toml": 'active_model = "x"'}
            ),
        ]
    )

    result = await fetch_managed_config("http://test", "api-key")

    assert result.error is None
    assert result.config is not None
    assert result.config.is_enabled is True
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_network_error_retries_then_reports_error(
    respx_mock: respx.MockRouter, no_retry_sleep: None
) -> None:
    route = respx_mock.get(_URL).mock(side_effect=httpx.ConnectError("boom"))

    result = await fetch_managed_config("http://test", "api-key")

    assert result.config is None
    assert result.error is not None
    assert route.call_count == MANAGED_CONFIG_RETRY_TRIES


@pytest.mark.asyncio
async def test_network_error_succeeds_on_retry(
    respx_mock: respx.MockRouter, no_retry_sleep: None
) -> None:
    route = respx_mock.get(_URL).mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(
                200, json={"state": "enabled", "toml": 'active_model = "x"'}
            ),
        ]
    )

    result = await fetch_managed_config("http://test", "api-key")

    assert result.error is None
    assert result.config is not None
    assert result.config.is_enabled is True
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_bad_json_reports_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(_URL).mock(return_value=httpx.Response(200, text="not json"))

    result = await fetch_managed_config("http://test", "api-key")

    assert result.config is None
    assert result.error is not None


def test_is_enabled_requires_toml() -> None:
    assert ManagedConfig(state="enabled", toml=None).is_enabled is False
    assert ManagedConfig(state="enabled", toml="").is_enabled is False
