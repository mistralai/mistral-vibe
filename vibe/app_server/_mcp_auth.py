"""Process-owned MCP authorization and interactive OAuth composition."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import time

from mcp.client.auth import OAuthFlowError

from vibe.app_server._session_backend_port import (
    MCPAuthorizationProvider,
    MCPAuthorizationRef,
    MCPAuthorizationRequired,
    MCPAuthorizationResult,
    MCPAuthorizationSnapshot,
)
from vibe.core.auth.mcp_oauth import (
    Fingerprint,
    KeyringTokenStorage,
    MCPOAuthError,
    MCPOAuthHeadlessError,
    MCPOAuthInvalidGrant,
    MCPOAuthTransientRefreshError,
    build_oauth_provider,
    delete_oauth_credentials,
    perform_oauth_login,
    restore_oauth_credentials,
    snapshot_oauth_credentials,
    unwrap_oauth_refresh_error,
)
from vibe.core.config import (
    MCPHttp,
    MCPOAuth,
    MCPServer,
    MCPStaticAuth,
    MCPStreamableHttp,
)
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

type RemoteMCPServer = MCPHttp | MCPStreamableHttp
type AuthURLSink = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _AuthorizationState:
    descriptor_generation: tuple[bool, int]
    connection_generation: tuple[bool, int]
    authorization_material: tuple[bool, str]
    rejected_material: tuple[bool, str]


class MCPAuthenticationService(MCPAuthorizationProvider):
    """Resolve transient headers while keeping credentials in the Vibe process."""

    def __init__(self) -> None:
        self._servers: dict[str, MCPServer] = {}
        self._fingerprints: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._descriptor_generations: dict[str, int] = {}
        self._connection_generations: dict[str, int] = {}
        self._authorization_material: dict[str, str] = {}
        self._rejected_material: dict[str, str] = {}

    async def bind_catalog(self, servers: Sequence[MCPServer]) -> None:
        """Install current app-owned definitions and invalidate changed identities."""
        active = {server.name: server for server in servers}
        for name, server in active.items():
            fingerprint = _server_fingerprint(server)
            previous = self._fingerprints.get(name)
            self._servers[name] = server
            self._fingerprints[name] = fingerprint
            if previous is None or previous == fingerprint:
                continue
            async with self._lock(name):
                self._advance_descriptor(name)
                self._advance_connection(name)
                self._authorization_material.pop(name, None)
                self._rejected_material.pop(name, None)
        removed = set(self._servers) - set(active)
        for name in removed:
            self._servers.pop(name, None)
            self._fingerprints.pop(name, None)
            self._authorization_material.pop(name, None)
            self._rejected_material.pop(name, None)

    def reference_for(self, server: MCPServer) -> MCPAuthorizationRef:
        kind = "none"
        if isinstance(server, MCPHttp | MCPStreamableHttp):
            kind = "oauth" if isinstance(server.auth, MCPOAuth) else "static"
        return MCPAuthorizationRef(
            server_name=server.name,
            server_fingerprint=_server_fingerprint(server),
            kind=kind,
            descriptor_revision=self.descriptor_revision(server.name),
        )

    async def resolve(self, reference: MCPAuthorizationRef) -> MCPAuthorizationResult:
        async with self._lock(reference.server_name):
            return await self._resolve_locked(reference)

    async def reject(
        self,
        reference: MCPAuthorizationRef,
        *,
        observed_connection_revision: str,
        reason: str,
    ) -> MCPAuthorizationResult:
        if reason not in {"http_unauthorized", "mcp_unauthorized"}:
            raise ValueError("Unsupported MCP authorization rejection reason")
        async with self._lock(reference.server_name):
            current = await self._resolve_locked(reference)
            if (
                isinstance(current, MCPAuthorizationSnapshot)
                and current.connection_revision != observed_connection_revision
            ):
                return current
            server = self._require_server(reference)
            material = self._authorization_material.get(reference.server_name, "")
            self._rejected_material[reference.server_name] = material
            if _is_oauth_server(server):
                await KeyringTokenStorage(alias=server.name).delete_tokens()
            self._advance_descriptor(server.name)
            self._advance_connection(server.name)
            return self._required(server.name, "rejected", observed_connection_revision)

    async def login(self, name: str, *, on_url: AuthURLSink) -> str:
        async with self._lock(name):
            server = self._require_oauth_server(name)
            await perform_oauth_login(server, on_url=on_url)
            self._advance_descriptor(name)
            self._advance_connection(name)
            self._authorization_material.pop(name, None)
            self._rejected_material.pop(name, None)
            return self.descriptor_revision(name)

    async def logout(self, name: str) -> str:
        async with self._lock(name):
            self._require_oauth_server(name)
            await self._delete_credentials_locked(name)
            return self.descriptor_revision(name)

    @asynccontextmanager
    async def credential_removal(self, name: str) -> AsyncIterator[str]:
        """Delete credentials before config and restore them if config removal aborts."""
        async with self._lock(name):
            self._require_oauth_server(name)
            backup = await snapshot_oauth_credentials(name)
            previous = self._authorization_state(name)
            try:
                await self._delete_credentials_locked(name)
                yield self.descriptor_revision(name)
            except BaseException:
                try:
                    await restore_oauth_credentials(name, backup)
                finally:
                    self._restore_authorization_state(name, previous)
                raise

    def descriptor_revision(self, name: str) -> str:
        fingerprint = self._fingerprints.get(name, "missing")
        generation = self._descriptor_generations.get(name, 0)
        return f"mcp-auth-descriptor:{fingerprint[:16]}:{generation}"

    async def _resolve_locked(
        self, reference: MCPAuthorizationRef
    ) -> MCPAuthorizationResult:
        server = self._require_server(reference)
        if reference.descriptor_revision != self.descriptor_revision(server.name):
            return self._required(server.name, "invalid")
        if reference.server_fingerprint != self._fingerprints.get(server.name):
            return self._required(server.name, "invalid")
        if not isinstance(server, MCPHttp | MCPStreamableHttp):
            return self._snapshot(server.name, {}, None)
        if isinstance(server.auth, MCPStaticAuth):
            return self._resolve_static(server)
        return await self._resolve_oauth(server)

    def _resolve_static(self, server: RemoteMCPServer) -> MCPAuthorizationResult:
        headers = server.http_headers()
        material = _authorization_material(headers)
        if self._rejected_material.get(server.name) == material:
            return self._required(server.name, "rejected")
        self._accept_material(server.name, material)
        return self._snapshot(server.name, headers, None)

    async def _resolve_oauth(  # noqa: PLR0911 - closed authorization outcomes
        self, server: RemoteMCPServer
    ) -> MCPAuthorizationResult:
        try:
            current_fingerprint = Fingerprint.compute(server)
            saved_fingerprint = await Fingerprint.load(server.name)
            storage = KeyringTokenStorage(alias=server.name)
            tokens = await storage.get_tokens()
        except MCPOAuthHeadlessError:
            return self._required(server.name, "missing")
        if saved_fingerprint != current_fingerprint:
            if tokens is not None or saved_fingerprint is not None:
                await delete_oauth_credentials(server.name)
                self._advance_descriptor(server.name)
                self._advance_connection(server.name)
            return self._required(server.name, "invalid")
        if tokens is None:
            return self._required(server.name, "missing")
        if (
            storage.token_expiry_time is not None
            and storage.token_expiry_time <= time.time()
        ):
            try:
                await self._refresh_oauth(server)
            except MCPOAuthInvalidGrant:
                self._advance_descriptor(server.name)
                self._advance_connection(server.name)
                return self._required(server.name, "expired")
            except (MCPOAuthTransientRefreshError, OAuthFlowError, MCPOAuthError):
                return self._required(server.name, "expired")
            tokens = await storage.get_tokens()
            if tokens is None:
                return self._required(server.name, "expired")
        headers = {
            **server.http_headers(),
            "Authorization": f"{tokens.token_type} {tokens.access_token}",
        }
        material = _authorization_material(headers)
        if self._rejected_material.get(server.name) == material:
            return self._required(server.name, "rejected")
        self._accept_material(server.name, material)
        expires_at = (
            datetime.fromtimestamp(storage.token_expiry_time, tz=UTC)
            if storage.token_expiry_time is not None
            else None
        )
        return self._snapshot(server.name, headers, expires_at)

    async def _refresh_oauth(self, server: RemoteMCPServer) -> None:
        async def reject_redirect(_url: str) -> None:
            raise OAuthFlowError("Interactive MCP OAuth login is required")

        async def reject_callback() -> tuple[str, str | None]:
            raise OAuthFlowError("Interactive MCP OAuth login is required")

        provider = build_oauth_provider(
            server, redirect_handler=reject_redirect, callback_handler=reject_callback
        )
        try:
            async with VibeAsyncHTTPClient(
                auth=provider,
                timeout=server.startup_timeout_sec,
                verify=build_ssl_context(),
            ) as client:
                await client.get(server.url)
        except Exception as exc:
            classified = unwrap_oauth_refresh_error(exc)
            if classified is not None:
                raise classified
            raise

    def _snapshot(
        self, name: str, headers: Mapping[str, str], expires_at: datetime | None
    ) -> MCPAuthorizationSnapshot:
        return MCPAuthorizationSnapshot(
            headers=headers,
            connection_revision=self.connection_revision(name),
            descriptor_revision=self.descriptor_revision(name),
            expires_at=expires_at,
        )

    def _required(
        self, name: str, reason: str, observed_connection_revision: str | None = None
    ) -> MCPAuthorizationRequired:
        if reason not in {"missing", "expired", "rejected", "invalid"}:
            raise ValueError("Unsupported MCP authorization requirement")
        return MCPAuthorizationRequired(
            reason=reason,  # pyright: ignore[reportArgumentType]
            descriptor_revision=self.descriptor_revision(name),
            observed_connection_revision=observed_connection_revision,
        )

    def connection_revision(self, name: str) -> str:
        generation = self._connection_generations.get(name, 0)
        return f"mcp-auth-connection:{name}:{generation}"

    def _accept_material(self, name: str, material: str) -> None:
        if self._authorization_material.get(name) == material:
            return
        self._authorization_material[name] = material
        self._rejected_material.pop(name, None)
        self._advance_connection(name)

    def _advance_descriptor(self, name: str) -> None:
        self._descriptor_generations[name] = (
            self._descriptor_generations.get(name, 0) + 1
        )

    def _advance_connection(self, name: str) -> None:
        self._connection_generations[name] = (
            self._connection_generations.get(name, 0) + 1
        )

    async def _delete_credentials_locked(self, name: str) -> None:
        await delete_oauth_credentials(name)
        self._advance_descriptor(name)
        self._advance_connection(name)
        self._authorization_material.pop(name, None)
        self._rejected_material.pop(name, None)

    def _authorization_state(self, name: str) -> _AuthorizationState:
        return _AuthorizationState(
            descriptor_generation=(
                name in self._descriptor_generations,
                self._descriptor_generations.get(name, 0),
            ),
            connection_generation=(
                name in self._connection_generations,
                self._connection_generations.get(name, 0),
            ),
            authorization_material=(
                name in self._authorization_material,
                self._authorization_material.get(name, ""),
            ),
            rejected_material=(
                name in self._rejected_material,
                self._rejected_material.get(name, ""),
            ),
        )

    def _restore_authorization_state(
        self, name: str, state: _AuthorizationState
    ) -> None:
        _restore_entry(self._descriptor_generations, name, state.descriptor_generation)
        _restore_entry(self._connection_generations, name, state.connection_generation)
        _restore_entry(self._authorization_material, name, state.authorization_material)
        _restore_entry(self._rejected_material, name, state.rejected_material)

    def _lock(self, name: str) -> asyncio.Lock:
        return self._locks.setdefault(name, asyncio.Lock())

    def _require_server(self, reference: MCPAuthorizationRef) -> MCPServer:
        server = self._servers.get(reference.server_name)
        if server is None:
            raise ValueError(f"Unknown MCP server: {reference.server_name}")
        return server

    def _require_oauth_server(self, name: str) -> RemoteMCPServer:
        server = self._servers.get(name)
        if not isinstance(server, MCPHttp | MCPStreamableHttp) or not isinstance(
            server.auth, MCPOAuth
        ):
            raise ValueError(f"MCP server {name!r} is not configured for OAuth")
        return server


def _is_oauth_server(server: object) -> bool:
    return isinstance(server, MCPHttp | MCPStreamableHttp) and isinstance(
        server.auth, MCPOAuth
    )


def _server_fingerprint(server: MCPServer) -> str:
    if isinstance(server, MCPHttp | MCPStreamableHttp):
        auth = server.auth
        auth_identity: object
        if isinstance(auth, MCPOAuth):
            auth_identity = Fingerprint.compute(server).model_dump(mode="json")
        else:
            auth_identity = {
                "type": "static",
                "header_names": sorted(auth.headers),
                "api_key_env": auth.api_key_env,
                "api_key_header": auth.api_key_header,
                "api_key_format": auth.api_key_format,
            }
        value = {
            "name": server.name,
            "transport": server.transport,
            "url": server.url,
            "auth": auth_identity,
            "prompt": server.prompt,
            "startup_timeout_sec": server.startup_timeout_sec,
            "tool_timeout_sec": server.tool_timeout_sec,
            "sampling_enabled": server.sampling_enabled,
        }
    else:
        value = server.model_dump(mode="json", exclude={"env"})
        value["env_names"] = sorted(server.env)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _authorization_material(headers: Mapping[str, str]) -> str:
    encoded = json.dumps(dict(headers), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _restore_entry[T](
    values: dict[str, T], name: str, previous: tuple[bool, T]
) -> None:
    present, value = previous
    if present:
        values[name] = value
    else:
        values.pop(name, None)


__all__ = ["MCPAuthenticationService"]
