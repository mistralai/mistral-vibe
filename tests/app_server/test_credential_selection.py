from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

from pydantic import SecretStr
import pytest

from vibe.app_server._connection_protocol import (
    ClientCapabilities,
    ClientInfo,
    PlatformCredentialsCapability,
)
from vibe.app_server._credential_selection import (
    ConnectionCredentialSelection,
    select_platform_credentials,
)
from vibe.app_server._platform_credentials import ConnectionPlatformCredentialProvider
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server.client import AppServerClient
from vibe.app_server.transport import memory_transport_pair
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialRequired,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)
from vibe.setup.auth._cli_oidc_platform_credentials import (
    CliOidcPlatformCredentialProvider,
)
from vibe.setup.auth._oidc_credentials import OidcTokens
from vibe.setup.auth._oidc_tenant import OidcTenantResolver
from vibe.utils import AgentEntrypoint
from vibe.utils.oidc_marker import OIDC_API_KEY


def _client(entrypoint: AgentEntrypoint) -> ClientInfo:
    return ClientInfo(name="test", version="1", entrypoint=entrypoint)


def _select(
    entrypoint: AgentEntrypoint | None,
    *,
    env_var: str = "MISTRAL_API_KEY",
    connection: ConnectionPlatformCredentialProvider | None = None,
) -> object:
    return select_platform_credentials(
        client=None if entrypoint is None else _client(entrypoint),
        connection=connection,
        tenants=OidcTenantResolver(),
        api_key_env_var=env_var,
    )


@pytest.mark.parametrize("entrypoint", ["cli", "programmatic", "acp"])
def test_cli_clients_use_the_cli_session_under_the_flag(monkeypatch, entrypoint):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    assert isinstance(_select(entrypoint), CliOidcPlatformCredentialProvider)
    monkeypatch.delenv("VIBE_CLI_OIDC_ENABLED")
    assert isinstance(_select(entrypoint), ApiKeyPlatformCredentialProvider)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["desktop", "unknown", None])
async def test_other_clients_never_borrow_the_cli_session(monkeypatch, entrypoint):
    # A stored CLI sign-in and the flag leaked into a non-CLI connection's
    # environment: the API-key fallback must not turn the marker into the CLI
    # user's ID token.
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setattr(
        "vibe.setup.auth._oidc_runtime.stored_oidc_credential", lambda: OIDC_API_KEY
    )
    monkeypatch.setattr(
        "vibe.setup.auth._oidc_runtime.oidc_headers",
        AsyncMock(side_effect=AssertionError("the CLI session was read")),
    )
    provider = _select(entrypoint)
    assert isinstance(provider, ApiKeyPlatformCredentialProvider)

    resolved = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    assert resolved == PlatformCredentialRequired(reason="missing")
    assert not provider.has_credential()
    assert provider.cached_principal() is None


@pytest.mark.asyncio
async def test_the_cli_process_api_key_provider_still_resolves_the_marker(monkeypatch):
    # Callers outside any connection (the default factory) keep the CLI
    # session the marker stands for.
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setattr(
        "vibe.setup.auth._oidc_runtime.stored_oidc_credential", lambda: OIDC_API_KEY
    )
    monkeypatch.setattr(
        "vibe.setup.auth._oidc_runtime.stored_oidc_cache_key", lambda: None
    )
    monkeypatch.setattr(
        "vibe.setup.auth._oidc_runtime.oidc_headers",
        AsyncMock(return_value={"Authorization": "Bearer cli-id-token"}),
    )

    resolved = await ApiKeyPlatformCredentialProvider("MISTRAL_API_KEY").resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.headers["Authorization"] == "Bearer cli-id-token"


def test_a_connection_provider_wins_over_the_cli_session(monkeypatch):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")

    async def unused(_: object) -> object:
        raise AssertionError

    connection = ConnectionPlatformCredentialProvider(
        unused,  # type: ignore[arg-type]
        tenants=OidcTenantResolver(),
    )
    assert _select("cli", connection=connection) is connection


def test_other_providers_keep_their_api_key(monkeypatch):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    assert isinstance(
        _select("cli", env_var="GATEWAY_API_KEY"), ApiKeyPlatformCredentialProvider
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entrypoint", "desktop_broker", "expected"),
    [
        ("cli", False, CliOidcPlatformCredentialProvider),
        ("desktop", False, ApiKeyPlatformCredentialProvider),
        ("desktop", True, ConnectionPlatformCredentialProvider),
    ],
)
async def test_each_connection_selects_its_own_provider(
    monkeypatch, process_services: ProcessServices, entrypoint, desktop_broker, expected
):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        process=HarnessProcess(services=process_services),
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    capabilities = ClientCapabilities(
        platform_credentials=PlatformCredentialsCapability(
            version=1, kind="oauth_access_token"
        )
        if desktop_broker
        else None
    )
    await client.initialize(_client(entrypoint), capabilities)
    await client.notify("initialized")
    try:
        provider = server._server.platform_credentials("MISTRAL_API_KEY")
        assert isinstance(provider, expected)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_bound_selection_reaches_host_reads(
    monkeypatch, process_services: ProcessServices
):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    process = HarnessProcess(services=process_services)
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport, transport_kind="in_process", process=process
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(_client("cli"), ClientCapabilities())
    await client.notify("initialized")
    try:
        assert isinstance(
            process.credentials("MISTRAL_API_KEY"), CliOidcPlatformCredentialProvider
        )
        assert isinstance(
            process.credentials("GATEWAY_API_KEY"), ApiKeyPlatformCredentialProvider
        )
    finally:
        await client.close()


def _signed_in_cli(monkeypatch) -> None:
    """A stored CLI sign-in with an explicit organization: no network."""
    from vibe.setup.auth import _cli_oidc_platform_credentials as cli_oidc
    from vibe.setup.auth._oidc_config import OidcConfig

    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "org")
    config = OidcConfig.from_env()
    tokens = OidcTokens(
        issuer=config.issuer,
        client_id=config.client_id,
        audience=config.audience,
        subject="user",
        access_token=SecretStr("access"),
        refresh_token=SecretStr("refresh"),
        id_token=SecretStr("cli-id-token"),
        id_token_expires_at=time.time() + 3600,
        expires_at=time.time() + 3600,
    )
    monkeypatch.setattr(cli_oidc, "_fresh_id_token", lambda: "cli-id-token")
    monkeypatch.setattr(cli_oidc, "_stored_tokens", lambda: tokens)


async def _cli_connection(process: HarnessProcess) -> tuple[Any, AppServerClient]:
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport, transport_kind="in_process", process=process
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(_client("cli"), ClientCapabilities())
    await client.notify("initialized")
    return server, client


@pytest.mark.asyncio
async def test_a_token_the_platform_rejected_stays_withheld_on_the_connection(
    monkeypatch, process_services: ProcessServices
):
    """*Prepare*: A CLI connection signed in under the OIDC flag.
    *Do*: Send its token, have the platform refuse it (401), then ask the
      connection for its credential again; then reconnect.
    *Assert*: The connection withholds the refused token with "rejected"
      rather than re-sending it; a new connection starts afresh.
    """
    # Prepare
    _signed_in_cli(monkeypatch)
    process = HarnessProcess(services=process_services)
    target = PlatformCredentialTarget("https://api.mistral.ai/v1")
    server, client = await _cli_connection(process)
    try:
        sent = await process.credentials("MISTRAL_API_KEY").resolve(target)
        assert isinstance(sent, PlatformCredentialSnapshot)

        # Do
        await server._server.platform_credentials("MISTRAL_API_KEY").reject(
            target, observed=sent.identity, reason="http_unauthorized"
        )
        again = await process.credentials("MISTRAL_API_KEY").resolve(target)

        # Assert
        assert again == PlatformCredentialRequired(
            reason="rejected", observed_revision=sent.revision
        )
    finally:
        await client.close()

    server, client = await _cli_connection(process)
    try:
        fresh = await process.credentials("MISTRAL_API_KEY").resolve(target)
        assert isinstance(fresh, PlatformCredentialSnapshot)
    finally:
        await client.close()


def test_a_connection_keeps_its_cli_provider_but_reads_api_keys_afresh(monkeypatch):
    """*Prepare*: A CLI connection's selection.
    *Do*: Ask for the Mistral credential twice, then turn the flag off and
      ask twice more.
    *Assert*: The same CLI provider answers both calls; without the flag an
      API-key provider answers, built afresh each call as before.
    """
    # Prepare
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    selection = ConnectionCredentialSelection(
        client=_client("cli"), connection=None, tenants=OidcTenantResolver()
    )

    # Do
    first = selection("MISTRAL_API_KEY")
    second = selection("MISTRAL_API_KEY")
    monkeypatch.delenv("VIBE_CLI_OIDC_ENABLED")
    api_key = selection("MISTRAL_API_KEY")

    # Assert
    assert isinstance(first, CliOidcPlatformCredentialProvider)
    assert second is first
    assert isinstance(api_key, ApiKeyPlatformCredentialProvider)
    assert selection("MISTRAL_API_KEY") is not api_key
