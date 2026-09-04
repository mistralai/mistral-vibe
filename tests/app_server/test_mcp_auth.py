from __future__ import annotations

import gc
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
    MCPOAuthHeadlessError,
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


def _plugin_oauth_server() -> MCPHttp:
    return MCPHttp(
        name="figma",
        transport="http",
        url="https://mcp.figma.test",
        auth=MCPOAuth(type="oauth", scopes=["read"]),
    )


@pytest.mark.asyncio
async def test_a_plugin_server_survives_every_config_only_catalog_read() -> None:
    # Prepare
    service = MCPAuthenticationService()
    await service.bind_plugin_catalog([_plugin_oauth_server()])

    # Do
    await service.bind_catalog([_oauth_server()])

    # Assert
    assert service.reference_for(_plugin_oauth_server()).kind == "oauth"
    with patch(
        "vibe.app_server._mcp_auth.perform_oauth_login", new=AsyncMock()
    ) as login:
        await service.login("figma", on_url=AsyncMock())
    assert login.await_args is not None
    assert login.await_args.args[0].name == "figma"


@pytest.mark.asyncio
async def test_a_config_server_survives_a_plugin_catalog_rebind() -> None:
    # Prepare
    service = MCPAuthenticationService()
    await service.bind_catalog([_oauth_server()])

    # Do
    await service.bind_plugin_catalog([_plugin_oauth_server()])

    # Assert
    with patch(
        "vibe.app_server._mcp_auth.perform_oauth_login", new=AsyncMock()
    ) as login:
        await service.login("linear", on_url=AsyncMock())
    assert login.await_args is not None
    assert login.await_args.args[0].name == "linear"


@pytest.mark.asyncio
async def test_a_plugin_server_no_longer_declared_is_dropped() -> None:
    # Prepare
    service = MCPAuthenticationService()
    await service.bind_plugin_catalog([_plugin_oauth_server()])

    # Do
    await service.bind_plugin_catalog([])

    # Assert
    with pytest.raises(ValueError, match="not configured for OAuth"):
        await service.login("figma", on_url=AsyncMock())


def _plugin_declared_server() -> MCPHttp:
    return MCPHttp(
        name="figma",
        transport="http",
        url="https://mcp.figma.test",
        auth=MCPStaticAuth(headers={"X-Figma-Plugin-Bundle": "figma_prod@2_2_96"}),
    )


def _late_bound_fingerprint() -> Fingerprint:
    server = _plugin_declared_server()
    return Fingerprint.compute(
        server.model_copy(update={"auth": MCPOAuth(type="oauth", scopes=[])})
    )


@pytest.mark.asyncio
async def test_a_plugin_server_that_declares_no_auth_can_still_be_logged_into() -> None:
    # Prepare
    service = MCPAuthenticationService()
    await service.bind_plugin_catalog([_plugin_declared_server()])

    # Do
    with patch(
        "vibe.app_server._mcp_auth.perform_oauth_login", new=AsyncMock()
    ) as login:
        await service.login("figma", on_url=AsyncMock())

    # Assert
    assert login.await_args is not None
    logged_in = login.await_args.args[0]
    assert logged_in.name == "figma"
    assert logged_in.url == _plugin_declared_server().url
    assert logged_in.auth == MCPOAuth(type="oauth", scopes=[])


@pytest.mark.asyncio
async def test_a_config_server_that_declares_static_auth_is_never_late_bound() -> None:
    # Prepare
    service = MCPAuthenticationService()
    await service.bind_catalog([_static_server()])

    # Do / Assert
    with pytest.raises(ValueError, match="not configured for OAuth"):
        await service.login("linear", on_url=AsyncMock())


@pytest.mark.asyncio
async def test_a_plugin_server_connects_on_its_declared_headers_before_any_login() -> (
    None
):
    # Prepare
    service = MCPAuthenticationService()
    server = _plugin_declared_server()
    await service.bind_plugin_catalog([server])
    storage = AsyncMock()
    storage.get_tokens.return_value = None
    storage.token_expiry_time = None

    # Do
    with (
        patch.object(Fingerprint, "load", new=AsyncMock(return_value=None)),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
    ):
        result = await service.resolve(service.reference_for(server, owner="plugin"))

    # Assert
    assert isinstance(result, MCPAuthorizationSnapshot)
    assert result.headers == {"X-Figma-Plugin-Bundle": "figma_prod@2_2_96"}


@pytest.mark.asyncio
async def test_a_plugin_reference_is_never_answered_with_a_config_servers_tokens() -> (
    None
):
    # Prepare
    service = MCPAuthenticationService()
    plugin_server = _plugin_declared_server()
    config_server = MCPHttp(
        name=plugin_server.name,
        transport="http",
        url="https://mcp.example.test",
        auth=MCPOAuth(type="oauth", scopes=["read"]),
    )
    await service.bind_catalog([config_server])
    await service.bind_plugin_catalog([plugin_server])
    storage = AsyncMock()
    storage.get_tokens.return_value = OAuthToken(
        access_token="the-users-grant", token_type="Bearer"
    )
    storage.token_expiry_time = None

    # Do
    with (
        # What the keyring holds is the configured server's grant, current and
        # usable, so nothing but the catalog split keeps it from being handed out.
        patch.object(
            Fingerprint,
            "load",
            new=AsyncMock(return_value=Fingerprint.compute(config_server)),
        ),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
    ):
        result = await service.resolve(
            service.reference_for(plugin_server, owner="plugin")
        )

    # Assert
    assert isinstance(result, MCPAuthorizationSnapshot)
    assert result.headers == {"X-Figma-Plugin-Bundle": "figma_prod@2_2_96"}
    assert "Authorization" not in result.headers


@pytest.mark.asyncio
async def test_a_headless_host_still_connects_a_plugin_server_it_cannot_log_in() -> (
    None
):
    # Prepare
    service = MCPAuthenticationService()
    server = _plugin_declared_server()
    await service.bind_plugin_catalog([server])
    headless = MCPOAuthHeadlessError(server_alias=server.name)

    # Do
    with patch("vibe.app_server._mcp_auth.KeyringTokenStorage", side_effect=headless):
        result = await service.resolve(service.reference_for(server, owner="plugin"))

    # Assert
    assert isinstance(result, MCPAuthorizationSnapshot)
    assert result.headers == {"X-Figma-Plugin-Bundle": "figma_prod@2_2_96"}


@pytest.mark.asyncio
async def test_a_logged_in_plugin_server_sends_its_declared_headers_with_the_bearer() -> (
    None
):
    # Prepare
    service = MCPAuthenticationService()
    server = _plugin_declared_server()
    await service.bind_plugin_catalog([server])
    storage = AsyncMock()
    storage.get_tokens.return_value = OAuthToken(
        access_token="granted", token_type="Bearer"
    )
    storage.token_expiry_time = None

    # Do
    with (
        patch.object(
            Fingerprint, "load", new=AsyncMock(return_value=_late_bound_fingerprint())
        ),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
    ):
        result = await service.resolve(service.reference_for(server, owner="plugin"))

    # Assert
    assert isinstance(result, MCPAuthorizationSnapshot)
    assert result.headers == {
        "X-Figma-Plugin-Bundle": "figma_prod@2_2_96",
        "Authorization": "Bearer granted",
    }


@pytest.mark.asyncio
async def test_a_plugin_server_outlives_a_config_entry_that_took_its_name() -> None:
    # Prepare
    service = MCPAuthenticationService()
    server = _plugin_declared_server()
    await service.bind_plugin_catalog([server])
    storage = AsyncMock()
    storage.get_tokens.return_value = None
    storage.token_expiry_time = None

    # Do
    # ``/mcp add figma`` and then ``/mcp remove figma``: one fingerprint
    # namespace serves both catalogs, so what the configured entry wrote has to
    # be handed back rather than merely left behind.
    await service.bind_catalog([
        MCPHttp(
            name=server.name,
            transport="http",
            url="https://mcp.example.test",
            auth=MCPStaticAuth(headers={"X-Tenant": "workspace"}),
        )
    ])
    await service.bind_catalog([])
    with (
        patch.object(Fingerprint, "load", new=AsyncMock(return_value=None)),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
    ):
        result = await service.resolve(service.reference_for(server, owner="plugin"))

    # Assert
    assert isinstance(result, MCPAuthorizationSnapshot)
    assert result.headers == {"X-Figma-Plugin-Bundle": "figma_prod@2_2_96"}


class _Session:
    # Stands in for the per-session objects the service keys catalogs on: a
    # PluginMCPCatalog for what a session's plugins declared, a
    # ConfigOrchestrator for what it configured.
    pass


def _configured_figma(*, oauth: bool) -> MCPHttp:
    # The name a plugin declared, in another session's ``mcp_servers``, at a
    # url of that session's own.
    return MCPHttp(
        name="figma",
        transport="http",
        url="https://figma.internal.test",
        auth=MCPOAuth(type="oauth", scopes=["read"])
        if oauth
        else MCPStaticAuth(headers={"X-Tenant": "workspace"}),
    )


@pytest.mark.asyncio
async def test_a_session_declaring_no_plugin_servers_leaves_another_session_alone() -> (
    None
):
    # Prepare
    service = MCPAuthenticationService()
    server = _plugin_declared_server()
    first, second = _Session(), _Session()
    await service.bind_plugin_catalog([server], owner=first)
    reference = service.reference_for(server, owner="plugin")
    storage = AsyncMock()
    storage.get_tokens.return_value = None
    storage.token_expiry_time = None

    # Do
    # A second session opening in a project whose plugins declare nothing.
    await service.bind_plugin_catalog([], owner=second)

    # Assert
    with (
        patch.object(Fingerprint, "load", new=AsyncMock(return_value=None)),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
    ):
        result = await service.resolve(reference)
    assert isinstance(result, MCPAuthorizationSnapshot)
    with patch(
        "vibe.app_server._mcp_auth.perform_oauth_login", new=AsyncMock()
    ) as login:
        await service.login("figma", on_url=AsyncMock())
    assert login.await_args is not None


@pytest.mark.asyncio
async def test_a_name_two_sessions_declared_outlives_one_of_them() -> None:
    # Prepare
    service = MCPAuthenticationService()
    server = _plugin_declared_server()
    first, second = _Session(), _Session()
    await service.bind_plugin_catalog([server], owner=first)
    await service.bind_plugin_catalog([server], owner=second)
    storage = AsyncMock()
    storage.get_tokens.return_value = None
    storage.token_expiry_time = None

    # Do
    await service.bind_plugin_catalog([], owner=first)

    # Assert
    with (
        patch.object(Fingerprint, "load", new=AsyncMock(return_value=None)),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
    ):
        result = await service.resolve(service.reference_for(server, owner="plugin"))
    assert isinstance(result, MCPAuthorizationSnapshot)


@pytest.mark.asyncio
async def test_a_session_that_ended_takes_the_servers_it_declared_with_it() -> None:
    # Prepare
    service = MCPAuthenticationService()
    server = _plugin_declared_server()
    ended = _Session()
    await service.bind_plugin_catalog([server], owner=ended)

    # Do
    # Nothing unbinds a session's plugin catalog, so a name it declared may not
    # outlive it: the login it answers is one no session is offering.
    del ended
    gc.collect()

    # Assert
    with pytest.raises(ValueError, match="not configured for OAuth"):
        await service.login("figma", on_url=AsyncMock())


@pytest.mark.asyncio
async def test_a_late_bound_login_is_challenged_with_the_declared_headers() -> None:
    # Prepare
    service = MCPAuthenticationService()
    server = _plugin_declared_server()
    await service.bind_plugin_catalog([server])

    # Do
    with patch(
        "vibe.app_server._mcp_auth.perform_oauth_login", new=AsyncMock()
    ) as login:
        await service.login("figma", on_url=AsyncMock())

    # Assert
    assert login.await_args is not None
    # Not on the server the login runs against: late binding rewrote that one to
    # OAuth, which holds no headers at all.
    assert login.await_args.args[0].auth == MCPOAuth(type="oauth", scopes=[])
    assert login.await_args.kwargs["headers"] == {
        "X-Figma-Plugin-Bundle": "figma_prod@2_2_96"
    }


@pytest.mark.asyncio
async def test_a_plugin_login_ignores_a_name_another_session_configured() -> None:
    # Prepare
    service = MCPAuthenticationService()
    plugins, elsewhere = _Session(), _Session()
    await service.bind_plugin_catalog([_plugin_declared_server()], owner=plugins)

    # Do
    # A second session, in a project of its own, configures that name for
    # itself. Nothing about this session changed.
    await service.bind_catalog([_configured_figma(oauth=True)], owner=elsewhere)

    # Assert
    with patch(
        "vibe.app_server._mcp_auth.perform_oauth_login", new=AsyncMock()
    ) as login:
        await service.login("figma", on_url=AsyncMock(), owner=plugins)
    assert login.await_args is not None
    assert login.await_args.args[0].url == "https://mcp.figma.test"
    assert login.await_args.kwargs["headers"] == {
        "X-Figma-Plugin-Bundle": "figma_prod@2_2_96"
    }


@pytest.mark.asyncio
async def test_a_static_entry_in_another_session_does_not_block_the_late_bind() -> None:
    # Prepare
    service = MCPAuthenticationService()
    plugins, elsewhere = _Session(), _Session()
    await service.bind_plugin_catalog([_plugin_declared_server()], owner=plugins)

    # Do
    # Static, so it is a configured decision not to do OAuth -- but only for the
    # session that made it.
    await service.bind_catalog([_configured_figma(oauth=False)], owner=elsewhere)

    # Assert
    with patch(
        "vibe.app_server._mcp_auth.perform_oauth_login", new=AsyncMock()
    ) as login:
        await service.login("figma", on_url=AsyncMock(), owner=plugins)
    assert login.await_args is not None
    assert login.await_args.args[0].auth == MCPOAuth(type="oauth", scopes=[])


@pytest.mark.asyncio
async def test_a_second_sessions_configured_servers_do_not_evict_the_firsts() -> None:
    # Prepare
    service = MCPAuthenticationService()
    first, second = _Session(), _Session()
    server = _static_server()
    await service.bind_catalog([server], owner=first)
    reference = service.reference_for(server)

    # Do
    # A session whose own ``mcp_servers`` name none of these: another project,
    # or a ``session/new`` that carried its own list.
    await service.bind_catalog([], owner=second)

    # Assert
    # Not a catalog read: a tool call, which is the whole of what a session does
    # with a bound server and rebinds nothing.
    assert isinstance(await service.resolve(reference), MCPAuthorizationSnapshot)


@pytest.mark.asyncio
async def test_a_shared_name_is_not_resolved_by_deleting_the_other_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Prepare
    service = MCPAuthenticationService()
    plugins, elsewhere = _Session(), _Session()
    await service.bind_plugin_catalog([_plugin_declared_server()], owner=plugins)
    configured = _configured_figma(oauth=True)
    await service.bind_catalog([configured], owner=elsewhere)
    storage = AsyncMock()
    storage.get_tokens.return_value = OAuthToken(
        access_token="what-the-plugin-session-logged-in-with", token_type="Bearer"
    )
    storage.token_expiry_time = None
    cleanup = AsyncMock()
    monkeypatch.setattr(_mcp_auth, "delete_oauth_credentials", cleanup)

    # Do
    # The configured session resolving its own server, over the keyring entry
    # the plugin session's login left under the one name they share.
    with (
        patch.object(
            Fingerprint, "load", new=AsyncMock(return_value=_late_bound_fingerprint())
        ),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
    ):
        result = await service.resolve(service.reference_for(configured))

    # Assert
    # Refused, because the fingerprint covers the url and this one is not its
    # own -- but refused without taking the other session's grant down with it.
    assert isinstance(result, MCPAuthorizationRequired)
    assert result.reason == "invalid"
    cleanup.assert_not_awaited()
