from __future__ import annotations

import httpx
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_identity_gateway import FakeIdentityGateway
from vibe.app_server._identity import (
    HttpIdentityGateway,
    IdentityController,
    IdentityGatewayUnauthorized,
    IdentityGatewayUnavailable,
    IdentityResult,
)
from vibe.app_server.models import IdentityView
from vibe.app_server.protocol import AppServerResponseError, IdentityReadParams
from vibe.core.types import Backend


@pytest.mark.asyncio
async def test_identity_controller_projects_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    agent_loop = build_test_agent_loop()
    gateway = FakeIdentityGateway(
        IdentityResult.model_validate({
            "id": "user-1",
            "email": "ada@example.com",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "workspace": {"id": "ws-1", "name": "Analytical Engine"},
            "organization": {"id": "org-1", "name": "Mistral"},
        })
    )

    try:
        identity = await IdentityController(agent_loop, gateway).read()
    finally:
        await agent_loop.aclose()

    assert identity is not None
    assert identity.id == "user-1"
    assert identity.name == "Ada Lovelace"
    assert identity.workspace is not None
    assert identity.workspace.name == "Analytical Engine"
    assert identity.organization is not None
    assert identity.organization.name == "Mistral"
    assert gateway.calls == [("https://api.mistral.ai/v1", "server-secret")]


@pytest.mark.asyncio
async def test_identity_controller_returns_none_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_loop = build_test_agent_loop()
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    gateway = FakeIdentityGateway(IdentityResult(id="user-1"))

    try:
        identity = await IdentityController(agent_loop, gateway).read()
    finally:
        await agent_loop.aclose()

    assert identity is None
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_identity_controller_skips_non_mistral_active_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    base_config = build_test_vibe_config()
    provider = base_config.get_active_provider().model_copy(
        update={"backend": Backend.GENERIC}
    )
    config = base_config.model_copy(update={"providers": [provider]})
    agent_loop = build_test_agent_loop(config=config)
    gateway = FakeIdentityGateway(IdentityResult(id="user-1"))

    try:
        identity = await IdentityController(agent_loop, gateway).read()
    finally:
        await agent_loop.aclose()

    assert identity is None
    assert gateway.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gateway",
    [FakeIdentityGateway(unauthorized=True), FakeIdentityGateway(unavailable=True)],
)
async def test_identity_controller_returns_none_on_gateway_failure(
    monkeypatch: pytest.MonkeyPatch, gateway: FakeIdentityGateway
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    agent_loop = build_test_agent_loop()

    try:
        identity = await IdentityController(agent_loop, gateway).read()
    finally:
        await agent_loop.aclose()

    assert identity is None


@pytest.mark.asyncio
async def test_identity_controller_logs_unauthorized_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    agent_loop = build_test_agent_loop()

    try:
        with caplog.at_level("INFO", logger="vibe"):
            identity = await IdentityController(
                agent_loop, FakeIdentityGateway(unauthorized=True)
            ).read()
    finally:
        await agent_loop.aclose()

    assert identity is None
    assert any(
        "401/403" in record.message and "invalid or expired" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_identity_controller_reuses_experiment_cached_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    agent_loop = build_test_agent_loop()
    cached = IdentityResult.model_validate({
        "id": "user-1",
        "first_name": "Ada",
        "organization": {"id": "org-1", "name": "Mistral"},
    })
    # Experiment init already fetched identity for the mistral endpoint + key.
    await agent_loop.identity_cache.resolve(
        base_url="https://api.mistral.ai/v1",
        api_key="server-secret",
        gateway=FakeIdentityGateway(cached),
    )

    # The controller must reuse it rather than issue a second /users/me call.
    controller_gateway = FakeIdentityGateway(IdentityResult(id="should-not-be-used"))
    try:
        identity = await IdentityController(agent_loop, controller_gateway).read()
    finally:
        await agent_loop.aclose()

    assert identity is not None
    assert identity.id == "user-1"
    assert controller_gateway.calls == []


@pytest.mark.asyncio
async def test_resolve_identity_caches_success_and_peeks() -> None:
    agent_loop = build_test_agent_loop()
    gateway = FakeIdentityGateway(IdentityResult(id="user-1"))
    try:
        assert (
            agent_loop.identity_cache.peek(
                base_url="https://api.mistral.ai/v1", api_key="k"
            )
            is None
        )
        first = await agent_loop.identity_cache.resolve(
            base_url="https://api.mistral.ai/v1", api_key="k", gateway=gateway
        )
        second = await agent_loop.identity_cache.resolve(
            base_url="https://api.mistral.ai/v1", api_key="k", gateway=gateway
        )
    finally:
        await agent_loop.aclose()

    assert first is second
    assert gateway.calls == [("https://api.mistral.ai/v1", "k")]  # single fetch
    assert (
        agent_loop.identity_cache.peek(
            base_url="https://api.mistral.ai/v1", api_key="k"
        )
        is first
    )


@pytest.mark.asyncio
async def test_resolve_identity_does_not_cache_failure() -> None:
    agent_loop = build_test_agent_loop()
    gateway = FakeIdentityGateway(unavailable=True)
    try:
        first = await agent_loop.identity_cache.resolve(
            base_url="https://api.mistral.ai/v1", api_key="k", gateway=gateway
        )
        second = await agent_loop.identity_cache.resolve(
            base_url="https://api.mistral.ai/v1", api_key="k", gateway=gateway
        )
    finally:
        await agent_loop.aclose()

    assert first is None
    assert second is None
    assert len(gateway.calls) == 2  # retried, not cached
    assert (
        agent_loop.identity_cache.peek(
            base_url="https://api.mistral.ai/v1", api_key="k"
        )
        is None
    )


@pytest.mark.parametrize(
    ("first_name", "last_name", "email", "expected"),
    [
        ("Ada", "Lovelace", "ada@example.com", "Ada Lovelace"),
        ("Ada", None, "ada@example.com", "Ada"),
        (None, "Lovelace", "ada@example.com", "ada@example.com"),
        (None, None, "ada@example.com", "ada@example.com"),
        (None, None, None, None),
    ],
)
def test_identity_view_name_resolution(
    first_name: str | None,
    last_name: str | None,
    email: str | None,
    expected: str | None,
) -> None:
    view = IdentityView(
        id="user-1", email=email, first_name=first_name, last_name=last_name
    )
    assert view.name == expected


@pytest.mark.asyncio
async def test_identity_resource_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    agent_loop = build_test_agent_loop()
    gateway = FakeIdentityGateway(
        IdentityResult(id="user-1", email="ada@example.com", first_name="Ada")
    )
    session = await create_test_app_server_session(agent_loop, identity_gateway=gateway)

    try:
        identity = await session.resources.identity.read()
    finally:
        await session.close()

    assert identity is not None
    assert identity.name == "Ada"
    request_json = IdentityReadParams(session_id="session").model_dump_json()
    assert "server-secret" not in request_json


@pytest.mark.asyncio
async def test_identity_resource_clears_current_when_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    agent_loop = build_test_agent_loop()

    class FlakyGateway:
        def __init__(self) -> None:
            self._calls = 0

        async def read(
            self, *, base_url: str, api_key: str, timeout: float | None = None
        ) -> IdentityResult:
            self._calls += 1
            if self._calls == 1:
                return IdentityResult(
                    id="user-1", email="ada@example.com", first_name="Ada"
                )
            raise RuntimeError("gateway blew up")

    session = await create_test_app_server_session(
        agent_loop, identity_gateway=FlakyGateway()
    )

    try:
        first = await session.resources.identity.read()
        assert first is not None
        assert first.name == "Ada"
        assert session.resources.identity.current is not None

        with pytest.raises(AppServerResponseError):
            await session.resources.identity.read()
        assert session.resources.identity.current is None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_http_identity_gateway_parses_response(respx_mock) -> None:
    route = respx_mock.get("https://api.mistral.ai/v1/users/me").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "user-1",
                "email": "ada@example.com",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "workspace": {"id": "ws-1", "name": "Analytical Engine"},
                "organization": {"id": "org-1", "name": "Mistral"},
            },
        )
    )

    result = await HttpIdentityGateway().read(
        base_url="https://api.mistral.ai/v1", api_key="server-secret"
    )

    assert result.id == "user-1"
    assert result.workspace is not None
    assert result.workspace.name == "Analytical Engine"
    assert route.calls.last.request.headers["Authorization"] == "Bearer server-secret"


@pytest.mark.asyncio
async def test_http_identity_gateway_ignores_extra_response_fields(respx_mock) -> None:
    respx_mock.get("https://api.mistral.ai/v1/users/me").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "user-1",
                "email": "ada@example.com",
                "api_key": "leaked-secret",
            },
        )
    )

    result = await HttpIdentityGateway().read(
        base_url="https://api.mistral.ai/v1", api_key="server-secret"
    )

    assert result == IdentityResult(id="user-1", email="ada@example.com")
    assert "leaked-secret" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_http_identity_gateway_maps_unauthorized_status(
    respx_mock, status_code: int
) -> None:
    respx_mock.get("https://api.mistral.ai/v1/users/me").mock(
        return_value=httpx.Response(status_code)
    )

    with pytest.raises(IdentityGatewayUnauthorized):
        await HttpIdentityGateway().read(
            base_url="https://api.mistral.ai/v1", api_key="server-secret"
        )


@pytest.mark.asyncio
async def test_http_identity_gateway_maps_server_error_to_unavailable(
    respx_mock,
) -> None:
    respx_mock.get("https://api.mistral.ai/v1/users/me").mock(
        return_value=httpx.Response(500)
    )

    with pytest.raises(IdentityGatewayUnavailable):
        await HttpIdentityGateway().read(
            base_url="https://api.mistral.ai/v1", api_key="server-secret"
        )
