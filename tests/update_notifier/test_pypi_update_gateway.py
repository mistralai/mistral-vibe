from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from vibe.cli.update_notifier.adapters.pypi_update_gateway import PyPIUpdateGateway
from vibe.cli.update_notifier.ports.update_gateway import (
    Update,
    UpdateGatewayCause,
    UpdateGatewayError,
)

Handler = Callable[[httpx.Request], httpx.Response]

PYPI_API_URL = "https://pypi.org"


@pytest.mark.asyncio
async def test_retrieves_nothing_when_no_versions_are_available() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=httpx.codes.OK, json={"versions": [], "files": []}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url=PYPI_API_URL) as client:
        gateway = PyPIUpdateGateway(project_name="mistral-vibe", client=client)
        update = await gateway.fetch_update()

    assert update is None


@pytest.mark.asyncio
async def test_retrieves_the_latest_non_yanked_version() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept"] == "application/vnd.pypi.simple.v1+json"
        assert request.url.path == "/simple/mistral-vibe/"
        return httpx.Response(
            status_code=httpx.codes.OK,
            json={
                "versions": ["1.0.0", "1.0.1", "1.0.2"],
                "files": [
                    {
                        "filename": "mistral_vibe-1.0.0-py3-none-any.whl",
                        "yanked": False,
                    },
                    {"filename": "mistral_vibe-1.0.1-py3-none-any.whl", "yanked": True},
                    {
                        "filename": "mistral_vibe-1.0.2-py3-none-any.whl",
                        "yanked": False,
                    },
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url=PYPI_API_URL) as client:
        gateway = PyPIUpdateGateway(project_name="mistral-vibe", client=client)
        update = await gateway.fetch_update()

    assert update == Update(latest_version="1.0.2")


@pytest.mark.asyncio
async def test_retrieves_nothing_when_only_yanked_versions_are_available() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=httpx.codes.OK,
            json={
                "versions": ["1.0.0"],
                "files": [
                    {"filename": "mistral_vibe-1.0.0-py3-none-any.whl", "yanked": True}
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url=PYPI_API_URL) as client:
        gateway = PyPIUpdateGateway(project_name="mistral-vibe", client=client)
        update = await gateway.fetch_update()

    assert update is None


@pytest.mark.asyncio
async def test_does_not_match_versions_by_substring() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=httpx.codes.OK,
            json={
                "versions": ["1.0.1"],
                "files": [
                    {
                        "filename": "mistral_vibe-1.0.10-py3-none-any.whl",
                        "yanked": False,
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url=PYPI_API_URL) as client:
        gateway = PyPIUpdateGateway(project_name="mistral-vibe", client=client)
        update = await gateway.fetch_update()

    assert update is None


def _raise_connect_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("boom", request=request)


@pytest.mark.parametrize(
    ("handler", "expected_cause", "expected_message"),
    [
        (
            lambda _: httpx.Response(status_code=httpx.codes.NOT_FOUND),
            UpdateGatewayCause.NOT_FOUND,
            None,
        ),
        (
            lambda _: httpx.Response(status_code=httpx.codes.FORBIDDEN),
            UpdateGatewayCause.FORBIDDEN,
            None,
        ),
        (
            lambda _: httpx.Response(status_code=httpx.codes.INTERNAL_SERVER_ERROR),
            UpdateGatewayCause.ERROR_RESPONSE,
            None,
        ),
        (
            lambda _: httpx.Response(status_code=httpx.codes.OK, content=b"{not-json"),
            UpdateGatewayCause.INVALID_RESPONSE,
            None,
        ),
        (_raise_connect_timeout, UpdateGatewayCause.REQUEST_FAILED, None),
    ],
)
@pytest.mark.asyncio
async def test_retrieves_nothing_when_fetching_update_fails(
    handler: Callable[[httpx.Request], httpx.Response],
    expected_cause: UpdateGatewayCause,
    expected_message: str | None,
) -> None:
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url=PYPI_API_URL) as client:
        gateway = PyPIUpdateGateway(project_name="mistral-vibe", client=client)
        with pytest.raises(UpdateGatewayError) as excinfo:
            await gateway.fetch_update()

    assert excinfo.value.cause == expected_cause
    if expected_message is not None:
        assert str(excinfo.value) == expected_message
