from __future__ import annotations

import logging

import httpx
import pytest
import respx

from vibe.core.experiments._constants import build_eval_url
from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.models import ExperimentAttributes

_TEST_API_HOST = "https://growthbook.test"
_TEST_CLIENT_KEY = "sdk-test"
_TEST_EVAL_URL = build_eval_url(_TEST_API_HOST, _TEST_CLIENT_KEY)
assert _TEST_EVAL_URL is not None


def _attrs() -> ExperimentAttributes:
    return ExperimentAttributes(
        userId="hashed",
        entrypoint="cli",
        agent_version="1.2.3",
        client_name="vibe",
        client_version="1.2.3",
        os="darwin",
    )


def _make_client() -> RemoteEvalClient:
    return RemoteEvalClient.from_settings(_TEST_API_HOST, _TEST_CLIENT_KEY)


@pytest.mark.asyncio
@respx.mock
async def test_evaluate_happy_path() -> None:
    payload = {
        "features": {
            ExperimentName.SYSTEM_PROMPT.value: {
                "defaultValue": "cli",
                "rules": [{"force": "cli_v2", "tracks": []}],
            }
        }
    }
    route = respx.post(_TEST_EVAL_URL).mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = _make_client()
    response = await client.evaluate(_attrs())
    await client.aclose()

    assert route.called
    request_body = route.calls.last.request.read()
    assert b'"userId":"hashed"' in request_body
    assert b'"forcedVariations":{}' in request_body
    assert b'"forcedFeatures":[]' in request_body
    assert response is not None
    assert (
        response.features[ExperimentName.SYSTEM_PROMPT.value].resolved_value()
        == "cli_v2"
    )


@pytest.fixture
def _silence_vibe_logger(caplog: pytest.LogCaptureFixture) -> None:
    # Failure-path tests legitimately emit WARNING logs. Silence the vibe
    # logger so they don't leak into ~/.vibe/logs/vibe.log (the file handler
    # is bound to the real path at module import, before VIBE_HOME is
    # monkeypatched in the conftest fixture).
    caplog.set_level(logging.CRITICAL, logger="vibe")


@pytest.mark.asyncio
@respx.mock
async def test_evaluate_returns_none_on_5xx(_silence_vibe_logger: None) -> None:
    respx.post(_TEST_EVAL_URL).mock(return_value=httpx.Response(500, text="oops"))
    client = _make_client()
    assert await client.evaluate(_attrs()) is None
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_evaluate_returns_none_on_network_error(
    _silence_vibe_logger: None,
) -> None:
    respx.post(_TEST_EVAL_URL).mock(side_effect=httpx.ConnectError("nope"))
    client = _make_client()
    assert await client.evaluate(_attrs()) is None
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_evaluate_returns_none_on_invalid_json(
    _silence_vibe_logger: None,
) -> None:
    respx.post(_TEST_EVAL_URL).mock(return_value=httpx.Response(200, text="not json"))
    client = _make_client()
    assert await client.evaluate(_attrs()) is None
    await client.aclose()


@pytest.mark.asyncio
async def test_evaluate_skips_request_when_url_unset() -> None:
    client = RemoteEvalClient.from_settings(api_host="", client_key="sdk-x")
    assert await client.evaluate(_attrs()) is None
    await client.aclose()


@pytest.mark.asyncio
async def test_aclose_is_idempotent() -> None:
    client = _make_client()
    await client.aclose()
    await client.aclose()
