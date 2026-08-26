from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

from mcp.shared.auth import OAuthToken
import pytest

from vibe.app_server import _mcp_auth
from vibe.app_server._mcp_auth import MCPAuthenticationService
from vibe.app_server._session_backend_port import (
    MCPAuthorizationRequired,
    MCPAuthorizationSnapshot,
)
from vibe.core.auth.mcp_oauth import (
    Fingerprint,
    MCPOAuthCredentialRestoreFailed,
    MCPOAuthInvalidGrant,
    MCPOAuthTransientRefreshError,
)
from vibe.core.config import MCPHttp, MCPOAuth, MCPStaticAuth, MCPStdio
from vibe.core.config.types import ConcurrencyConflictError


def _static_server(*, url: str = "https://mcp.example.test") -> MCPHttp:
    return MCPHttp(
        name="linear",
        transport="http",
        url=url,
        auth=MCPStaticAuth(
            headers={"X-Tenant": "workspace"}, api_key_env="LINEAR_TOKEN"
        ),
    )


def _oauth_server() -> MCPHttp:
    return MCPHttp(
        name="linear",
        transport="http",
        url="https://mcp.example.test",
        auth=MCPOAuth(type="oauth", scopes=["read"]),
    )


@pytest.mark.asyncio
async def test_static_authorization_resolves_headers_and_environment_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINEAR_TOKEN", "secret")
    service = MCPAuthenticationService()
    server = _static_server()
    await service.bind_catalog([server])

    result = await service.resolve(service.reference_for(server))

    assert isinstance(result, MCPAuthorizationSnapshot)
    assert result.headers == {"X-Tenant": "workspace", "Authorization": "Bearer secret"}


@pytest.mark.asyncio
async def test_environment_token_change_advances_only_connection_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MCPAuthenticationService()
    server = _static_server()
    await service.bind_catalog([server])
    reference = service.reference_for(server)
    monkeypatch.setenv("LINEAR_TOKEN", "one")
    first = await service.resolve(reference)
    monkeypatch.setenv("LINEAR_TOKEN", "two")
    second = await service.resolve(reference)

    assert isinstance(first, MCPAuthorizationSnapshot)
    assert isinstance(second, MCPAuthorizationSnapshot)
    assert second.connection_revision != first.connection_revision
    assert second.descriptor_revision == first.descriptor_revision
    assert second.headers["Authorization"] == "Bearer two"


@pytest.mark.asyncio
async def test_stale_rejection_returns_newer_authorization_without_invalidating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MCPAuthenticationService()
    server = _static_server()
    await service.bind_catalog([server])
    reference = service.reference_for(server)
    monkeypatch.setenv("LINEAR_TOKEN", "one")
    stale = await service.resolve(reference)
    monkeypatch.setenv("LINEAR_TOKEN", "two")
    current = await service.resolve(reference)
    assert isinstance(stale, MCPAuthorizationSnapshot)
    assert isinstance(current, MCPAuthorizationSnapshot)

    rejected = await service.reject(
        reference,
        observed_connection_revision=stale.connection_revision,
        reason="http_unauthorized",
    )

    assert rejected == current


@pytest.mark.asyncio
async def test_current_static_rejection_advances_descriptor_and_requires_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINEAR_TOKEN", "one")
    service = MCPAuthenticationService()
    server = _static_server()
    await service.bind_catalog([server])
    reference = service.reference_for(server)
    current = await service.resolve(reference)
    assert isinstance(current, MCPAuthorizationSnapshot)

    rejected = await service.reject(
        reference,
        observed_connection_revision=current.connection_revision,
        reason="mcp_unauthorized",
    )

    assert isinstance(rejected, MCPAuthorizationRequired)
    assert rejected.reason == "rejected"
    assert rejected.observed_connection_revision == current.connection_revision
    assert rejected.descriptor_revision != current.descriptor_revision
    assert isinstance(await service.resolve(reference), MCPAuthorizationRequired)


@pytest.mark.asyncio
async def test_changed_catalog_fingerprint_rejects_stale_reference() -> None:
    service = MCPAuthenticationService()
    original = _static_server()
    await service.bind_catalog([original])
    stale_reference = service.reference_for(original)
    changed = _static_server(url="https://changed.example.test")
    await service.bind_catalog([changed])

    result = await service.resolve(stale_reference)

    assert isinstance(result, MCPAuthorizationRequired)
    assert result.reason == "invalid"


@pytest.mark.asyncio
async def test_credential_removal_rolls_back_keyring_and_authorization_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: An OAuth source with an opaque keyring backup and accepted revision.
    *Do*: Delete credentials, then abort the enclosing config removal with a conflict.
    *Assert*: Credentials and the prior authorization revision are restored.
    """
    # Prepare
    service = MCPAuthenticationService()
    server = _oauth_server()
    await service.bind_catalog([server])
    reference = service.reference_for(server)
    fingerprint = Fingerprint.compute(server)
    storage = AsyncMock()
    storage.get_tokens.return_value = OAuthToken(
        access_token="accepted", token_type="Bearer"
    )
    storage.token_expiry_time = None
    backup = object()
    snapshot = AsyncMock(return_value=backup)
    cleanup = AsyncMock()
    restore = AsyncMock()
    monkeypatch.setattr(_mcp_auth, "snapshot_oauth_credentials", snapshot)
    monkeypatch.setattr(_mcp_auth, "delete_oauth_credentials", cleanup)
    monkeypatch.setattr(_mcp_auth, "restore_oauth_credentials", restore)

    # Do
    with (
        patch.object(Fingerprint, "load", new=AsyncMock(return_value=fingerprint)),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
    ):
        previous = await service.resolve(reference)
        with pytest.raises(ConcurrencyConflictError):
            async with service.credential_removal(server.name):
                assert service.descriptor_revision(server.name) != (
                    previous.descriptor_revision
                )
                raise ConcurrencyConflictError("expected", "actual")
        restored = await service.resolve(reference)

    # Assert
    assert isinstance(previous, MCPAuthorizationSnapshot)
    assert restored == previous
    snapshot.assert_awaited_once_with(server.name)
    cleanup.assert_awaited_once_with(server.name)
    restore.assert_awaited_once_with(server.name, backup)


@pytest.mark.asyncio
async def test_credential_removal_restores_authorization_state_when_keyring_restore_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: An OAuth source whose keyring restore fails after config removal aborts.
    *Do*: Exit credential removal through the config failure path.
    *Assert*: In-process authorization state rolls back and both failures remain chained.
    """
    # Prepare
    service = MCPAuthenticationService()
    server = _oauth_server()
    await service.bind_catalog([server])
    previous_revision = service.descriptor_revision(server.name)
    backup = object()
    restore_failure = MCPOAuthCredentialRestoreFailed(
        server_alias=server.name, reason="injected restore failure"
    )
    monkeypatch.setattr(
        _mcp_auth, "snapshot_oauth_credentials", AsyncMock(return_value=backup)
    )
    monkeypatch.setattr(_mcp_auth, "delete_oauth_credentials", AsyncMock())
    monkeypatch.setattr(
        _mcp_auth, "restore_oauth_credentials", AsyncMock(side_effect=restore_failure)
    )

    # Do
    with pytest.raises(MCPOAuthCredentialRestoreFailed) as exc_info:
        async with service.credential_removal(server.name):
            assert service.descriptor_revision(server.name) != previous_revision
            raise ConcurrencyConflictError("expected", "actual")

    # Assert
    assert service.descriptor_revision(server.name) == previous_revision
    assert isinstance(exc_info.value.__context__, ConcurrencyConflictError)


@pytest.mark.asyncio
async def test_stdio_authorization_never_exposes_environment() -> None:
    service = MCPAuthenticationService()
    server = MCPStdio(
        name="local", transport="stdio", command="server", env={"SECRET": "value"}
    )
    await service.bind_catalog([server])

    result = await service.resolve(service.reference_for(server))

    assert isinstance(result, MCPAuthorizationSnapshot)
    assert result.headers == {}


@pytest.mark.asyncio
async def test_oauth_missing_credentials_returns_typed_requirement() -> None:
    service = MCPAuthenticationService()
    server = _oauth_server()
    await service.bind_catalog([server])
    fingerprint = Fingerprint.compute(server)
    storage = AsyncMock()
    storage.get_tokens.return_value = None
    storage.token_expiry_time = None

    with (
        patch.object(Fingerprint, "load", new=AsyncMock(return_value=fingerprint)),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
    ):
        result = await service.resolve(service.reference_for(server))

    assert isinstance(result, MCPAuthorizationRequired)
    assert result.reason == "missing"


@pytest.mark.asyncio
async def test_oauth_refresh_publishes_fresh_token_and_expiry() -> None:
    service = MCPAuthenticationService()
    server = _oauth_server()
    await service.bind_catalog([server])
    fingerprint = Fingerprint.compute(server)
    storage = AsyncMock()
    storage.get_tokens.side_effect = [
        OAuthToken(access_token="old", token_type="Bearer"),
        OAuthToken(access_token="fresh", token_type="Bearer"),
    ]
    storage.token_expiry_time = time.time() - 1

    with (
        patch.object(Fingerprint, "load", new=AsyncMock(return_value=fingerprint)),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
        patch.object(service, "_refresh_oauth", new=AsyncMock()) as refresh,
    ):
        result = await service.resolve(service.reference_for(server))

    assert isinstance(result, MCPAuthorizationSnapshot)
    assert result.headers["Authorization"] == "Bearer fresh"
    assert result.expires_at is not None
    refresh.assert_awaited_once_with(server)


@pytest.mark.parametrize(
    ("failure", "advances_descriptor"),
    [
        (MCPOAuthInvalidGrant(server_alias="linear", reason="invalid_grant"), True),
        (MCPOAuthTransientRefreshError(server_alias="linear", reason="503"), False),
    ],
)
@pytest.mark.asyncio
async def test_oauth_refresh_failure_is_typed_and_only_invalid_grant_invalidates(
    failure: Exception, advances_descriptor: bool
) -> None:
    service = MCPAuthenticationService()
    server = _oauth_server()
    await service.bind_catalog([server])
    reference = service.reference_for(server)
    before = service.descriptor_revision(server.name)
    fingerprint = Fingerprint.compute(server)
    storage = AsyncMock()
    storage.get_tokens.return_value = OAuthToken(
        access_token="old", token_type="Bearer"
    )
    storage.token_expiry_time = time.time() - 1

    with (
        patch.object(Fingerprint, "load", new=AsyncMock(return_value=fingerprint)),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
        patch.object(service, "_refresh_oauth", new=AsyncMock(side_effect=failure)),
    ):
        result = await service.resolve(reference)

    assert isinstance(result, MCPAuthorizationRequired)
    assert result.reason == "expired"
    assert (result.descriptor_revision != before) is advances_descriptor
