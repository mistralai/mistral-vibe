from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from tests.app_server._helpers import setup_client
from vibe.app_server.client import AppServerClient
from vibe.setup.auth import _oidc_runtime
from vibe.setup.auth._oidc_credentials import OidcTokens

_METHOD = "platformCredentials/headers"


@pytest_asyncio.fixture
async def cli_client(
    monkeypatch, tokens: OidcTokens, process_services
) -> AsyncIterator[AppServerClient]:
    monkeypatch.setattr(
        _oidc_runtime, "_resolve_id_token", lambda: tokens.id_token.get_secret_value()
    )
    client, client_transport, server_transport = await setup_client(
        entrypoint="cli", process_services=process_services
    )
    try:
        yield client
    finally:
        await client_transport.close()
        await server_transport.close()


@pytest.mark.asyncio
async def test_serves_the_cli_session_headers_for_first_party_audio(
    cli_client: AppServerClient, tokens: OidcTokens
) -> None:
    for url in (
        "https://api.mistral.ai/v1/audio/speech",
        "wss://api.mistral.ai/v1/audio/transcriptions/realtime",
    ):
        response = await cli_client.request(_METHOD, {"url": url})
        assert response["headers"] == {
            "Authorization": f"Bearer {tokens.id_token.get_secret_value()}",
            "x-external-organization-id": "test-org",
        }


@pytest.mark.asyncio
async def test_refuses_a_destination_outside_the_audience(
    cli_client: AppServerClient,
) -> None:
    with pytest.raises(Exception, match="invalid") as error:
        await cli_client.request(_METHOD, {"url": "https://attacker.example/v1"})
    assert "Bearer" not in str(error.value)


@pytest.mark.asyncio
async def test_refuses_non_cli_clients(monkeypatch, tokens, process_services) -> None:
    client, client_transport, server_transport = await setup_client(
        entrypoint="unknown", process_services=process_services
    )
    try:
        with pytest.raises(Exception, match="only served to CLI OIDC clients"):
            await client.request(_METHOD, {"url": "https://api.mistral.ai/v1"})
    finally:
        await client_transport.close()
        await server_transport.close()


@pytest.mark.asyncio
async def test_refuses_api_key_mode(monkeypatch, process_services) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ENABLED", raising=False)
    monkeypatch.setenv("MISTRAL_API_KEY", "api-key")
    client, client_transport, server_transport = await setup_client(
        entrypoint="cli", process_services=process_services
    )
    try:
        with pytest.raises(Exception, match="only served to CLI OIDC clients"):
            await client.request(_METHOD, {"url": "https://api.mistral.ai/v1"})
    finally:
        await client_transport.close()
        await server_transport.close()


@pytest.mark.asyncio
async def test_signed_out_reports_reauthentication_without_a_fallback(
    monkeypatch, tokens, process_services
) -> None:
    from vibe.setup.auth._oidc_credentials import OidcCredentialStore

    monkeypatch.setattr(OidcCredentialStore, "_load", lambda self: None)
    monkeypatch.setenv("MISTRAL_API_KEY", "api-key")
    client, client_transport, server_transport = await setup_client(
        entrypoint="cli", process_services=process_services
    )
    try:
        with pytest.raises(Exception, match="sign in again") as error:
            await client.request(_METHOD, {"url": "https://api.mistral.ai/v1"})
        assert "api-key" not in str(error.value)
    finally:
        await client_transport.close()
        await server_transport.close()
