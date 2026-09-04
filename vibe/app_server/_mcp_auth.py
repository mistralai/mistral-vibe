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
import weakref

from mcp.client.auth import OAuthFlowError

from vibe.app_server._session_backend_port import (
    MCPAuthorizationProvider,
    MCPAuthorizationRef,
    MCPAuthorizationRequired,
    MCPAuthorizationResult,
    MCPAuthorizationSnapshot,
    MCPCatalogOwner,
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


class _AnonymousCatalog:
    # Stands in for a caller that binds without saying which catalog the
    # servers came from -- a sessionless mutation above all, whose orchestrator
    # is built fresh per call and so cannot be a key anything holds.
    # Module-level below, so the weak maps never collect the one entry every
    # such caller shares. A bare ``object`` cannot be weakly referenced, which
    # is the only reason this is a class.
    __slots__ = ("__weakref__",)


_ANONYMOUS_CONFIG_CATALOG = _AnonymousCatalog()
_ANONYMOUS_PLUGIN_CATALOG = _AnonymousCatalog()

type _Catalogs = weakref.WeakKeyDictionary[object, dict[str, MCPServer]]


@dataclass(frozen=True, slots=True)
class _AuthorizationState:
    descriptor_generation: tuple[bool, int]
    connection_generation: tuple[bool, int]
    authorization_material: tuple[bool, str]
    rejected_material: tuple[bool, str]


class MCPAuthenticationService(MCPAuthorizationProvider):
    """Resolve transient headers while keeping credentials in the Vibe process."""

    def __init__(self) -> None:
        # Both keyed by the catalog that bound them, and weakly: one service
        # serves every session in the process, each binding its own configured
        # servers and its own plugins through it, and a session that ends
        # unbinds nothing. Weak keys let a dropped session's declarations go
        # with it rather than stay resolvable for the life of the process.
        self._config_catalogs: _Catalogs = weakref.WeakKeyDictionary()
        self._plugin_catalogs: _Catalogs = weakref.WeakKeyDictionary()
        self._fingerprints: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._descriptor_generations: dict[str, int] = {}
        self._connection_generations: dict[str, int] = {}
        self._authorization_material: dict[str, str] = {}
        self._rejected_material: dict[str, str] = {}

    async def bind_catalog(
        self, servers: Sequence[MCPServer], *, owner: object | None = None
    ) -> None:
        """Install a session's app-owned definitions and invalidate changed identities."""
        # Scoped to the session that bound them: ``mcp_servers`` is per-session
        # -- ``session/new`` carries its own list and each session reads the
        # config of its own cwd -- so a single map would let one session's
        # catalog evict the servers another session's tools resolve through,
        # and nothing short of that session reading its own catalog again would
        # bind them back.
        key = _ANONYMOUS_CONFIG_CATALOG if owner is None else owner
        await self._bind_owned(self._config_catalogs, key, servers)

    async def bind_plugin_catalog(
        self, servers: Sequence[MCPServer], *, owner: object | None = None
    ) -> None:
        # Separate from ``bind_catalog`` because each evicts every name it was
        # not given, and their callers hold one side alone. Folded together, a
        # plugin server would be dropped by the next config read. Scoped to its
        # owner for the same reason a configured one is.
        key = _ANONYMOUS_PLUGIN_CATALOG if owner is None else owner
        await self._bind_owned(self._plugin_catalogs, key, servers)

    async def _bind_owned(
        self, catalogs: _Catalogs, key: object, servers: Sequence[MCPServer]
    ) -> None:
        owned = catalogs.pop(key, {})
        # Reinserted at the end so the merged views name whichever server the
        # fingerprint installed below belongs to, where two owners bound the
        # same name.
        catalogs[key] = owned
        await self._bind(owned, self._survivors(excluding=key), servers)

    def _survivors(self, *, excluding: object) -> dict[str, MCPServer]:
        # Config last: a configured server outranks a plugin's under the same
        # name, so it is the one a departing binding hands that name back to. A
        # key lives in one map or the other, so excluding it from both is free.
        return self._plugin_view(excluding=excluding) | self._config_view(
            excluding=excluding
        )

    def _config_view(self, *, excluding: object | None = None) -> dict[str, MCPServer]:
        return _merged(self._config_catalogs, excluding)

    def _plugin_view(self, *, excluding: object | None = None) -> dict[str, MCPServer]:
        return _merged(self._plugin_catalogs, excluding)

    async def _bind(
        self,
        owned: dict[str, MCPServer],
        other: Mapping[str, MCPServer],
        servers: Sequence[MCPServer],
    ) -> None:
        active = {server.name: server for server in servers}
        for name, server in active.items():
            owned[name] = server
            await self._install_fingerprint(name, server)
        removed = set(owned) - set(active)
        for name in removed:
            owned.pop(name, None)
            survivor = other.get(name)
            if survivor is not None:
                # Handed back rather than left alone: ``_fingerprints`` is one
                # namespace for both catalogs, so what sits under a contested
                # name is whichever bound last -- here the departing server's.
                # Keeping it resolves every reference the survivor builds as
                # ``invalid``, including a rebuilt one, and binding is the only
                # writer: a plugin catalog binds at resolution and nowhere else,
                # so nothing in the session would ever correct it.
                await self._install_fingerprint(name, survivor)
                continue
            self._fingerprints.pop(name, None)
            self._authorization_material.pop(name, None)
            self._rejected_material.pop(name, None)

    async def _install_fingerprint(self, name: str, server: MCPServer) -> None:
        fingerprint = _server_fingerprint(server)
        previous = self._fingerprints.get(name)
        self._fingerprints[name] = fingerprint
        if previous is None or previous == fingerprint:
            return
        async with self._lock(name):
            self._advance_descriptor(name)
            self._advance_connection(name)
            self._authorization_material.pop(name, None)
            self._rejected_material.pop(name, None)

    def reference_for(
        self, server: MCPServer, *, owner: MCPCatalogOwner = "config"
    ) -> MCPAuthorizationRef:
        kind = "none"
        if isinstance(server, MCPHttp | MCPStreamableHttp):
            kind = "oauth" if isinstance(server.auth, MCPOAuth) else "static"
        return MCPAuthorizationRef(
            server_name=server.name,
            server_fingerprint=_server_fingerprint(server),
            kind=kind,
            descriptor_revision=self.descriptor_revision(server.name),
            owner=owner,
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
            # Skipped where the alias is shared: the stored credential may be
            # the one another owner's server of that name is connected with.
            # The rejection is recorded above either way, so this session still
            # stops retrying these headers and still asks for a login.
            if not self._alias_is_shared(server.name):
                await self._discard_rejected_credential(server, reference)
            self._advance_descriptor(server.name)
            self._advance_connection(server.name)
            return self._required(server.name, "rejected", observed_connection_revision)

    async def _discard_rejected_credential(
        self, server: MCPServer, reference: MCPAuthorizationRef
    ) -> None:
        if _is_oauth_server(server):
            await KeyringTokenStorage(alias=server.name).delete_tokens()
            return
        late_bound = self._late_bound_oauth(
            server, plugin_owned=reference.owner == "plugin"
        )
        if late_bound is None:
            return
        # Best-effort: one that never logged in has nothing stored, and a
        # headless host had nowhere to store it.
        await _discard_tokens(server.name)

    async def login(
        self, name: str, *, on_url: AuthURLSink, owner: object | None = None
    ) -> str:
        async with self._lock(name):
            server = self._require_oauth_server(name, owner)
            await perform_oauth_login(
                server, on_url=on_url, headers=self._declared_headers(name, owner)
            )
            self._advance_descriptor(name)
            self._advance_connection(name)
            self._authorization_material.pop(name, None)
            self._rejected_material.pop(name, None)
            return self.descriptor_revision(name)

    async def logout(self, name: str, *, owner: object | None = None) -> str:
        async with self._lock(name):
            self._require_oauth_server(name, owner)
            await self._delete_credentials_locked(name)
            return self.descriptor_revision(name)

    @asynccontextmanager
    async def credential_removal(
        self, name: str, *, owner: object | None = None
    ) -> AsyncIterator[str]:
        """Delete credentials before config and restore them if config removal aborts."""
        async with self._lock(name):
            self._require_oauth_server(name, owner)
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
            return await self._resolve_declared_static(
                server, plugin_owned=reference.owner == "plugin"
            )
        return await self._resolve_oauth(server, server.http_headers())

    async def _resolve_declared_static(
        self, server: RemoteMCPServer, *, plugin_owned: bool
    ) -> MCPAuthorizationResult:
        late_bound = self._late_bound_oauth(server, plugin_owned=plugin_owned)
        if late_bound is None:
            return self._resolve_static(server)
        result = await self._resolve_oauth(late_bound, server.http_headers())
        if isinstance(result, MCPAuthorizationRequired) and result.reason in {
            "missing",
            "invalid",
        }:
            # Nothing usable is stored and the plugin never said this server
            # does OAuth, so connect with what it declared and let the server's
            # own 401 ask for the login. A plugin authenticating through a
            # declared header keeps connecting untouched.
            return self._resolve_static(server)
        return result

    def _resolve_static(self, server: RemoteMCPServer) -> MCPAuthorizationResult:
        headers = server.http_headers()
        material = _authorization_material(headers)
        if self._rejected_material.get(server.name) == material:
            return self._required(server.name, "rejected")
        self._accept_material(server.name, material)
        return self._snapshot(server.name, headers, None)

    async def _resolve_oauth(  # noqa: PLR0911 - closed authorization outcomes
        self, server: RemoteMCPServer, declared_headers: Mapping[str, str]
    ) -> MCPAuthorizationResult:
        try:
            current_fingerprint = Fingerprint.compute(server)
            saved_fingerprint = await Fingerprint.load(server.name)
            storage = KeyringTokenStorage(alias=server.name)
            tokens = await storage.get_tokens()
        except MCPOAuthHeadlessError:
            return self._required(server.name, "missing")
        if saved_fingerprint != current_fingerprint:
            # What is stored was minted for something else under this name. Not
            # necessarily for an older shape of this server, though: where the
            # alias is shared it is another owner's live grant, and deleting it
            # would log that session out. Left alone, it is still never handed
            # over -- the fingerprint covers the url, and this returns below.
            stale = tokens is not None or saved_fingerprint is not None
            if stale and not self._alias_is_shared(server.name):
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
            **declared_headers,
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

    def _bound(self, name: str, owner: object | None) -> tuple[MCPServer | None, bool]:
        # The server a name typed at ``/mcp`` means, and whether a plugin owns
        # it. Answered inside the catalog the asking session bound, because
        # there is nothing else to disambiguate with and another session having
        # configured this name says nothing about what it means here.
        if owner is not None:
            plugins = self._plugin_catalogs.get(owner)
            if plugins is not None:
                return plugins.get(name), True
            configured = self._config_catalogs.get(owner)
            if configured is not None:
                return configured.get(name), False
        # Nobody said who is asking, or they never bound: a sessionless
        # mutation, which has no plugins. Config wins, as it does in a session.
        merged = self._config_view().get(name)
        if merged is not None:
            return merged, False
        return self._plugin_view().get(name), True

    def _owned_server(self, reference: MCPAuthorizationRef) -> MCPServer | None:
        # Never falls back to the other kind of catalog: that would answer a
        # plugin's reference with a configured server's credentials, which the
        # plugin would send to the url it declared. Absent reads as unknown,
        # which is the safe answer. Merged across owners because a reference
        # names no session; one built against another session's server of the
        # same name fails the fingerprint check below rather than resolving.
        catalog = (
            self._plugin_view() if reference.owner == "plugin" else self._config_view()
        )
        return catalog.get(reference.server_name)

    def _require_server(self, reference: MCPAuthorizationRef) -> MCPServer:
        server = self._owned_server(reference)
        if server is None:
            raise ValueError(f"Unknown MCP server: {reference.server_name}")
        return server

    def _require_oauth_server(self, name: str, owner: object | None) -> RemoteMCPServer:
        server, plugin_owned = self._bound(name, owner)
        if isinstance(server, MCPHttp | MCPStreamableHttp):
            if isinstance(server.auth, MCPOAuth):
                return server
            late_bound = self._late_bound_oauth(server, plugin_owned=plugin_owned)
            if late_bound is not None:
                return late_bound
        raise ValueError(f"MCP server {name!r} is not configured for OAuth")

    def _declared_headers(self, name: str, owner: object | None) -> Mapping[str, str]:
        # Read off the bound server, not off what the login is performed
        # against: a late-bound one is rewritten to ``MCPOAuth``, which holds no
        # headers, so the copy carries none of what the plugin declared. Resolve
        # merges them under the bearer and the challenge has to be the same
        # request, or a server that routes on them need not answer it with the
        # 401 the flow waits for.
        server, _ = self._bound(name, owner)
        if not isinstance(server, MCPHttp | MCPStreamableHttp):
            return {}
        return server.http_headers()

    def _late_bound_oauth(
        self, server: MCPServer, *, plugin_owned: bool
    ) -> RemoteMCPServer | None:
        # A plugin manifest describes a remote server with a url and headers
        # and nothing else, so both adapters build static auth and a 401 would
        # have no declared route to a login. Dynamic client registration is
        # what the MCP spec prescribes for that 401. Config-owned servers are
        # excluded: ``[[mcp_servers]]`` can say ``auth.type = "oauth"``, so a
        # static entry there is a decision.
        #
        # Whose the server is comes from the caller, which found it in one
        # catalog. Re-derived here from the process, a configured entry no
        # session but another one has would answer for it.
        if not plugin_owned:
            return None
        if not isinstance(server, MCPHttp | MCPStreamableHttp):
            return None
        if not isinstance(server.auth, MCPStaticAuth):
            return None
        return server.model_copy(update={"auth": MCPOAuth(type="oauth", scopes=[])})

    def _alias_is_shared(self, name: str) -> bool:
        # The keyring files a credential under the catalog name, and that name
        # is one namespace for the whole process. Where two owners answer to it
        # -- a plugin's server in one session, a configured entry in another --
        # they have a single credential between them, so a delete on behalf of
        # either destroys what the other is connected with. Inside one session
        # they cannot both hold it: a configured entry drops the plugin's.
        holders = 0
        for catalogs in (self._config_catalogs, self._plugin_catalogs):
            for servers in catalogs.values():
                holders += name in servers
                if holders > 1:
                    return True
        return False


def _merged(catalogs: _Catalogs, excluding: object | None) -> dict[str, MCPServer]:
    merged: dict[str, MCPServer] = {}
    for key, servers in catalogs.items():
        if key is excluding:
            continue
        merged.update(servers)
    return merged


async def _discard_tokens(name: str) -> None:
    try:
        await KeyringTokenStorage(alias=name).delete_tokens()
    except MCPOAuthHeadlessError:
        return


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
