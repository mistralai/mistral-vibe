"""MCP SDK subclasses, kept apart: the SDK import costs ~230 modules at startup."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Final, Literal

import httpx2
from mcp.client.auth import OAuthClientProvider, OAuthFlowError
from mcp.client.auth.oauth2 import OAuthContext
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_oauth_metadata_request,
    credentials_match_issuer,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    issuers_match,
    validate_metadata_issuer,
)

from vibe.core.auth.mcp_oauth import MCPOAuthInvalidGrant, MCPOAuthTransientRefreshError

if TYPE_CHECKING:
    from mcp.shared.auth import (
        AuthorizationCodeResult,
        OAuthClientMetadata,
        ProtectedResourceMetadata,
    )

    from vibe.core.auth.mcp_oauth import KeyringTokenStorage

# OAuth 2.0 token-endpoint error signalling a permanently dead refresh token.
_OAUTH_INVALID_GRANT: Final = "invalid_grant"


async def _classify_refresh_error(response: httpx2.Response) -> tuple[str, bool]:
    """Return a reason and whether the refresh failed permanently (invalid_grant)."""
    try:
        # body arrives unread in the httpx2 auth flow
        await response.aread()
        payload = response.json()
    except (ValueError, httpx2.HTTPError):
        return f"HTTP {response.status_code}", False
    if not isinstance(payload, dict):
        return f"HTTP {response.status_code}", False
    error = payload.get("error")
    description = payload.get("error_description") or ""
    reason = ": ".join(part for part in (error, description) if part) or (
        f"HTTP {response.status_code}"
    )
    return reason, error == _OAUTH_INVALID_GRANT


async def _fetch_metadata(client: httpx2.AsyncClient, url: str) -> httpx2.Response:
    # Unauthenticated: the client's OAuth auth would refresh before sending it.
    return await client.send(create_oauth_metadata_request(url), auth=None)


class _ServerIssuedSecretContext(OAuthContext):
    """``OAuthContext`` that authenticates a server-issued client secret.

    Some authorization servers (e.g. Supabase) accept a registration with
    ``token_endpoint_auth_method="none"`` but register a confidential client
    anyway: the response carries a ``client_secret`` while omitting the auth
    method, and their token endpoint then rejects requests without client
    authentication. RFC 7591 §2 defaults an omitted method to
    ``client_secret_basic``, and RFC 6749 §2.3.1 requires servers that issue
    a client password to support HTTP Basic, so pick the method from what the
    server advertises in ``token_endpoint_auth_methods_supported`` and only
    fall back to ``client_secret_post`` when Basic is not an option. The
    derived value is per request, never persisted, so keyring entries saved
    before this class existed (method absent or ``"none"``) self-heal.
    """

    def prepare_token_auth(
        self, data: dict[str, str], headers: dict[str, str] | None = None
    ) -> tuple[dict[str, str], dict[str, str]]:
        client_info = self.client_info
        if (
            client_info is not None
            and client_info.client_secret
            and client_info.token_endpoint_auth_method in {None, "none"}
        ):
            client_info.token_endpoint_auth_method = self._secret_auth_method()
        return super().prepare_token_auth(data, headers)

    def _secret_auth_method(
        self,
    ) -> Literal["client_secret_basic", "client_secret_post"]:
        supported = (
            self.oauth_metadata.token_endpoint_auth_methods_supported
            if self.oauth_metadata is not None
            else None
        )
        if not supported or "client_secret_basic" in supported:
            return "client_secret_basic"
        if "client_secret_post" in supported:
            return "client_secret_post"
        return "client_secret_basic"


class RefreshAwareOAuthClientProvider(OAuthClientProvider):
    """Like ``OAuthClientProvider`` but only clears tokens on a genuine ``invalid_grant``."""

    def __init__(
        self,
        server_url: str,
        client_metadata: OAuthClientMetadata,
        storage: KeyringTokenStorage,
        *,
        server_alias: str,
        redirect_handler: Callable[[str], Awaitable[None]] | None = None,
        callback_handler: Callable[[], Awaitable[AuthorizationCodeResult]]
        | None = None,
        client_metadata_url: str | None = None,
    ) -> None:
        super().__init__(
            server_url=server_url,
            client_metadata=client_metadata,
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            client_metadata_url=client_metadata_url,
        )
        # The base class builds a plain OAuthContext; swap it for the one that
        # authenticates a server-issued client secret on every token request.
        self.context = _ServerIssuedSecretContext(
            server_url=server_url,
            client_metadata=client_metadata,
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            client_metadata_url=client_metadata_url,
        )
        self._server_alias = server_alias
        self._storage = storage

    async def _initialize(self) -> None:
        await super()._initialize()
        self.context.token_expiry_time = self._storage.token_expiry_time

    async def discover_authorization_server(self, client: httpx2.AsyncClient) -> None:
        """Discover the token endpoint the SDK skips when it refreshes a stored token."""
        # Without it the SDK refreshes at `<MCP host>/token`, wrong wherever the
        # authorization server lives elsewhere. Same rules as login's discovery.
        try:
            protected_resource = await self._discover_protected_resource(client)
            auth_server_url = None
            if protected_resource is not None:
                await self._validate_resource_match(protected_resource)
                auth_server_url = self._select_authorization_server([
                    str(url) for url in protected_resource.authorization_servers
                ])
            self.context.auth_server_url = auth_server_url
            expected_issuer = self._expected_issuer()
            client_info = await self._storage.get_client_info()
            if client_info is not None and not credentials_match_issuer(
                client_info, expected_issuer, self.context.client_metadata_url
            ):
                raise OAuthFlowError(
                    "The MCP server moved to another authorization server"
                )
            for url in build_oauth_authorization_server_metadata_discovery_urls(
                auth_server_url, self.context.server_url
            ):
                ok, metadata = await handle_auth_metadata_response(
                    await _fetch_metadata(client, url)
                )
                if not ok:
                    return
                if metadata is None:
                    continue
                if auth_server_url is None and issuers_match(
                    str(metadata.issuer), expected_issuer
                ):
                    expected_issuer = str(metadata.issuer)
                validate_metadata_issuer(metadata, expected_issuer)
                self.context.protected_resource_metadata = protected_resource
                self.context.oauth_metadata = metadata
                return
        except httpx2.HTTPError:
            return

    async def _discover_protected_resource(
        self, client: httpx2.AsyncClient
    ) -> ProtectedResourceMetadata | None:
        for url in build_protected_resource_metadata_discovery_urls(
            None, self.context.server_url
        ):
            found = await handle_protected_resource_response(
                await _fetch_metadata(client, url)
            )
            if found is not None:
                return found
        return None

    async def _handle_refresh_response(self, response: httpx2.Response) -> bool:
        """Only clear stored tokens on a genuine invalid_grant error."""
        if response.status_code == httpx2.codes.OK:
            return await super()._handle_refresh_response(response)
        reason, is_invalid_grant = await _classify_refresh_error(response)
        if is_invalid_grant:
            self.context.clear_tokens()
            await self._storage.delete_tokens()
            await self._storage.delete_client_info()
            raise MCPOAuthInvalidGrant(server_alias=self._server_alias, reason=reason)
        raise MCPOAuthTransientRefreshError(
            server_alias=self._server_alias, reason=reason
        )
