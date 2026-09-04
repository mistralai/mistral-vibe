"""Plugin-owned MCP servers.

The registry routes each one under a private alias so two plugins declaring
``figma`` cannot collide. Nothing user-facing may use that alias: the keyring,
``/mcp`` and every login key on a catalog name instead -- the declared source
id where it is unambiguous, and a digest-suffixed form of it where a second
plugin declared the same one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Literal

from vibe.app_server._mcp_authorization_bridge import (
    RegistryAuthorizationProvider,
    registry_authorization_ref,
)

if TYPE_CHECKING:
    from pathlib import Path

    from vibe.app_server._mcp_auth import MCPAuthenticationService
    from vibe.core.config.models import MCPServer
    from vibe.core.plugins import PluginMCPDiscovery, PluginMCPServerDefinition
    from vibe.core.tools.mcp.registry import MCPRegistry
    from vibe.core.tools.remote import RemoteTool

logger = logging.getLogger(__name__)

type PluginMCPStatus = Literal["connected", "needs_auth", "unavailable"]


@dataclass(frozen=True, slots=True)
class PluginMCPTool:
    name: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class PluginMCPServerEntry:
    definition: PluginMCPServerDefinition
    # The declaration renamed to its catalog name, which is what the keyring
    # keys on. ``definition.server`` keeps the private alias the registry
    # routes by.
    server: MCPServer

    @property
    def name(self) -> str:
        return self.server.name

    @property
    def private_alias(self) -> str:
        return self.definition.private_alias

    @property
    def plugin_name(self) -> str:
        return self.definition.plugin_name

    @property
    def transport(self) -> str:
        return self.definition.server.transport


@dataclass(frozen=True, slots=True)
class PluginMCPSource:
    entry: PluginMCPServerEntry
    status: PluginMCPStatus
    tools: tuple[PluginMCPTool, ...] = ()
    error: str | None = None

    @property
    def name(self) -> str:
        return self.entry.name

    @property
    def plugin_name(self) -> str:
        return self.entry.plugin_name

    @property
    def transport(self) -> str:
        return self.entry.transport


class PluginMCPCatalog:
    def __init__(
        self,
        registry: MCPRegistry,
        authentication: MCPAuthenticationService,
        *,
        descriptor_cache_root: Path | None = None,
    ) -> None:
        self._registry = registry
        self._authentication = authentication
        self._descriptor_cache_root = descriptor_cache_root
        self._entries: dict[str, PluginMCPServerEntry] = {}
        self._names: dict[tuple[str, str], str] = {}
        self._sources: dict[str, PluginMCPSource] = {}

    @property
    def registry(self) -> MCPRegistry:
        return self._registry

    def entry(self, name: str) -> PluginMCPServerEntry | None:
        return self._entries.get(name)

    def name_for(self, definition: PluginMCPServerDefinition) -> str | None:
        return self._names.get((definition.plugin_name, definition.source_id))

    def aliases(self) -> dict[str, str]:
        return {
            name: entry.private_alias for name, entry in sorted(self._entries.items())
        }

    def names(self) -> dict[str, str]:
        return {
            entry.private_alias: name for name, entry in sorted(self._entries.items())
        }

    def sources(self) -> tuple[PluginMCPSource, ...]:
        # A server declared but never connected reports ``unavailable`` rather
        # than being left out, so a plugin's config is never invisible in /mcp.
        return tuple(
            self._sources.get(name)
            or PluginMCPSource(entry=entry, status="unavailable")
            for name, entry in sorted(self._entries.items())
        )

    async def bind(self, definitions: Sequence[PluginMCPServerDefinition]) -> None:
        # Must run before materialization: discovery happens inside it, and an
        # unarmed registry reports every OAuth server as unauthorizable rather
        # than merely unauthorized.
        names = _catalog_names(definitions)
        entries: dict[str, PluginMCPServerEntry] = {}
        for definition in definitions:
            name = names[(definition.plugin_name, definition.source_id)]
            entries[name] = _entry(definition, name)
        self._entries = entries
        self._names = names
        self._sources = {
            name: source for name, source in self._sources.items() if name in entries
        }
        # Bound in this catalog's name: the service is one per process and every
        # session resolves its own plugins through it, so an anonymous bind
        # would take the other sessions' servers down with the ones it replaces.
        await self._authentication.bind_plugin_catalog(
            [entry.server for entry in entries.values()], owner=self
        )
        self.configure()

    def configure(self) -> None:
        self._registry.configure_authorization(
            RegistryAuthorizationProvider(self._authentication),
            {
                entry.private_alias: registry_authorization_ref(
                    self._authentication.reference_for(entry.server, owner="plugin")
                )
                for entry in self._entries.values()
            },
            descriptor_cache_root=self._descriptor_cache_root,
        )

    def discovery(self) -> PluginMCPDiscovery:
        from vibe.core.plugins import RegistryMCPDiscovery

        return _RecordingDiscovery(self, RegistryMCPDiscovery(self._registry))

    async def refresh(self, name: str) -> PluginMCPSource | None:
        entry = self._entries.get(name)
        if entry is None:
            return None
        previous = self._sources.get(name)
        # Rebuilds the reference: it carries the descriptor revision it was
        # built at, and a credential change leaves a stale one resolving as
        # ``invalid``.
        self.configure()
        current = await self._rediscover(entry)
        # Closes the loop on plugin.mcp.authorization_required, which is baked
        # into a snapshot taken before the login and so cannot clear itself.
        if (
            current is not None
            and current.status == "connected"
            and (previous is None or previous.status != "connected")
        ):
            logger.info(
                "plugin.mcp.authenticated: MCP server %r declared by the %r plugin "
                "is connected with %d tools",
                name,
                entry.plugin_name,
                len(current.tools),
            )
        return current

    async def refresh_all(self) -> None:
        # What ``mcp_catalog/refresh`` means for a plugin's servers. Their
        # statuses are recorded at materialization and never expire, so without
        # this a retry reprojects the failure it was asked to clear.
        if not self._entries:
            return
        self.configure()
        for _, entry in sorted(self._entries.items()):
            await self._rediscover(entry)

    async def _rediscover(self, entry: PluginMCPServerEntry) -> PluginMCPSource | None:
        try:
            await self.discovery().discover(entry.definition)
        except Exception:
            # Discovery records the failure before it raises, so the status is
            # already current and there is nothing here to report.
            logger.debug(
                "Plugin MCP server %r did not answer a rediscovery",
                entry.name,
                exc_info=True,
            )
        return self._sources.get(entry.name)

    def record(
        self,
        name: str,
        *,
        status: PluginMCPStatus,
        tools: Iterable[PluginMCPTool] = (),
        error: str | None = None,
    ) -> None:
        entry = self._entries.get(name)
        if entry is None:
            return
        self._sources[name] = PluginMCPSource(
            entry=entry, status=status, tools=tuple(tools), error=error
        )


class _RecordingDiscovery:
    # ``build_tool_catalog`` turns what discovery raises into plugin
    # diagnostics and drops the outcome, but the /mcp status column needs the
    # same facts as data rather than as prose to parse.

    def __init__(self, catalog: PluginMCPCatalog, inner: PluginMCPDiscovery) -> None:
        self._catalog = catalog
        self._inner = inner

    async def discover(
        self, definition: PluginMCPServerDefinition
    ) -> tuple[RemoteTool, ...]:
        from vibe.core.plugins import (
            PluginMCPAuthorizationRequired,
            mcp_server_secrets,
            redact_failure,
        )

        try:
            tools = await self._inner.discover(definition)
        except PluginMCPAuthorizationRequired:
            self._record(definition, status="needs_auth")
            raise
        except Exception as error:
            # Authored by the MCP client, not by Vibe: an HTTP status error
            # quotes the request URL with its query string, secrets included.
            detail = (
                redact_failure(str(error), mcp_server_secrets(definition.server))
                or type(error).__name__
            )
            self._record(definition, status="unavailable", error=detail)
            raise
        self._record(
            definition,
            status="connected",
            tools=(
                PluginMCPTool(name=tool.name, description=tool.description)
                for tool in tools
            ),
        )
        return tools

    def _record(
        self,
        definition: PluginMCPServerDefinition,
        *,
        status: PluginMCPStatus,
        tools: Iterable[PluginMCPTool] = (),
        error: str | None = None,
    ) -> None:
        # Through the catalog name rather than the declared source id: two
        # plugins can declare the same id, and recording under it would put
        # this outcome on whichever of them holds that row.
        name = self._catalog.name_for(definition)
        if name is None:
            return
        self._catalog.record(name, status=status, tools=tools, error=error)


def _entry(definition: PluginMCPServerDefinition, name: str) -> PluginMCPServerEntry:
    # Renaming is safe for OAuth identity: the credential fingerprint covers
    # the url, the scopes and the client marker, never the name.
    return PluginMCPServerEntry(
        definition=definition,
        server=definition.server.model_copy(update={"name": name}),
    )


def _catalog_names(
    definitions: Sequence[PluginMCPServerDefinition],
) -> dict[tuple[str, str], str]:
    # Two plugins may both declare ``figma``. The tool layer already keeps both
    # -- its group names carry the plugin namespace -- so keying this catalog
    # by the declared id alone was the one place a server was lost. Resolved
    # through the helper the group names use, so a contested id is
    # disambiguated by that algorithm rather than by a second one able to drift
    # from it: neither side keeps the bare name, and the suffix each gets is a
    # digest of its own plugin, so discovery order cannot reach the result.
    from vibe.core.plugins import ToolGroupIdentity, resolve_tool_group_names

    identities = {
        (definition.plugin_name, definition.source_id): ToolGroupIdentity(
            plugin_name=definition.plugin_name,
            base_name=definition.source_id,
            source_id=definition.source_id,
        )
        for definition in definitions
    }
    resolved = resolve_tool_group_names(identities.values(), claimed=())
    return {key: resolved[identity] for key, identity in identities.items()}


__all__ = [
    "PluginMCPCatalog",
    "PluginMCPServerEntry",
    "PluginMCPSource",
    "PluginMCPStatus",
    "PluginMCPTool",
]
