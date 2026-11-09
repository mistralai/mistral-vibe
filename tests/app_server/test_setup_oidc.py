from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.app_server._helpers import setup_client
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._model import validate_wire
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._setup_oidc import SetupOidcHandler
from vibe.app_server.protocol import SetupStatusResponse
from vibe.setup.auth._oidc_service import OidcService


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["start", "poll", "cancel", "probe", "clear"])
async def test_disabled_oidc_never_constructs_service(monkeypatch, method):
    monkeypatch.delenv("VIBE_CLI_OIDC_ENABLED", raising=False)
    monkeypatch.setattr(OidcService, "from_env", lambda: pytest.fail("OIDC was loaded"))
    with pytest.raises(RequestFailure, match="disabled"):
        await SetupOidcHandler().dispatch(f"setup/oidc-{method}", {})


@pytest.mark.asyncio
async def test_oidc_rpc_projects_only_nonsecret_results(monkeypatch):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    attempt = SimpleNamespace(
        attempt_id="attempt",
        authorization_url="https://issuer.example/authorize",
        status="completed",
        detail=None,
    )
    service = SimpleNamespace(
        start=AsyncMock(return_value=attempt),
        poll=lambda _: attempt,
        cancel=AsyncMock(),
        probe=AsyncMock(return_value={"issuer": "issuer", "subject": "user"}),
        clear=AsyncMock(),
    )
    monkeypatch.setattr(OidcService, "from_env", lambda: service)
    handler = SetupOidcHandler()
    assert (await handler.dispatch("setup/oidc-start", {})).model_dump() == {
        "attemptId": "attempt"
    }
    assert (
        await handler.dispatch("setup/oidc-poll", {"attemptId": "attempt"})
    ).model_dump() == {
        "status": "completed",
        "authorizationUrl": "https://issuer.example/authorize",
        "detail": None,
    }
    assert (await handler.dispatch("setup/oidc-probe", {})).model_dump() == {
        "issuer": "issuer",
        "subject": "user",
    }
    await handler.dispatch("setup/oidc-clear", {})
    service.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_oidc_rpc_returns_id_then_polls_sanitized_initialization_failure(
    monkeypatch,
):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setenv("VIBE_CLI_OIDC_ISSUER", "https://issuer.example")
    monkeypatch.setenv("VIBE_CLI_OIDC_CLIENT_ID", "public-client")
    service = OidcService.from_env()

    def failing_session():
        raise ValueError("secret-token")

    monkeypatch.setattr(service.client, "session", failing_session)
    monkeypatch.setattr(OidcService, "from_env", lambda: service)
    handler = SetupOidcHandler()
    started = (await handler.dispatch("setup/oidc-start", {})).model_dump()
    assert set(started) == {"attemptId"}
    attempt_id = started["attemptId"]
    assert isinstance(attempt_id, str) and attempt_id
    assert (
        await handler.dispatch("setup/oidc-poll", {"attemptId": attempt_id})
    ).model_dump() == {"status": "waiting", "authorizationUrl": None, "detail": None}
    attempt = service.poll(attempt_id)
    assert attempt.task is not None
    await asyncio.wait_for(attempt.task, 1)
    polled = (
        await handler.dispatch("setup/oidc-poll", {"attemptId": attempt_id})
    ).model_dump()
    assert polled["status"] == "failed"
    assert polled["authorizationUrl"] is None
    assert polled["detail"] is not None
    assert "secret-token" not in str(polled)


@pytest.mark.asyncio
async def test_setup_status_flag_does_not_replace_api_key_presence(
    monkeypatch, process_services: ProcessServices
):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        status = validate_wire(
            SetupStatusResponse, await client.request("setup/status", {})
        )
        assert status.oidc_enabled is True
        assert status.has_api_key is False
    finally:
        await client_transport.close()
        await server_transport.close()
