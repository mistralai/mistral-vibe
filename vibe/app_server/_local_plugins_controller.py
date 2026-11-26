"""The ``plugins/local/*`` API: installed plugins listed and toggled from Desktop.

A plugin is addressed by its manifest name, which is also what
``disabled_plugins`` lists.

The user's AI Registry plugins are listed too, as the ``remote`` scope
(``vibe.core.plugins.remote``): those that are on resolve from their download,
those that are off from the last synced list. Their switch writes to le-chat,
the choice Vibe Work reads, rather than to ``disabled_plugins``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence, Set as AbstractSet
from dataclasses import replace
import json
import logging
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue, ValidationError

from vibe.app_server._dispatch import RequestFailure, method_not_found
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._projection import writable_list_field
from vibe.app_server._session_backend_port import (
    ResolvedConnector,
    ResolvedConnectorCatalog,
)
from vibe.app_server.connector_catalog import connector_readiness
from vibe.app_server.models import PluginComponentKind, PluginScope
from vibe.app_server.protocol import (
    LocalPluginComponent,
    LocalPluginConnector,
    LocalPluginConnectorStatus,
    LocalPluginIssue,
    LocalPluginStarterPrompt,
    LocalPluginSummary,
    LocalSkillRefusalData,
    PluginsLocalListParams,
    PluginsLocalListResponse,
    PluginsLocalSetEnabledParams,
    PluginsLocalSetEnabledResponse,
    ProtocolErrorCode,
    RemoteRegistryPluginRef,
)
from vibe.core.config import VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.models import MCPServer, MCPStdio
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.paths import VIBE_HOME
from vibe.core.platform_auth import PlatformCredentialFactory
from vibe.core.plugins import (
    InstalledPlugin,
    InstalledPluginStatus,
    PluginDescriptor,
    PluginResolver,
    PluginToggles,
    ResolvedPluginSet,
    is_required_plugin,
    set_plugin_enabled,
    user_facing_issues,
)
from vibe.core.plugins._compatibility import typescript_identifier
from vibe.core.plugins.remote import (
    RemotePlugin,
    RemotePluginsError,
    RemotePluginsState,
    current_remote_plugins_state,
    refresh_remote_plugins,
    remote_plugin_for_root,
    remote_plugin_pending,
    remote_plugins_read_error,
    remote_plugins_syncing,
    set_remote_plugin_enabled,
)
from vibe.core.plugins.unsupported import unsupported_parts
from vibe.core.plugins.views import plugin_views
from vibe.core.skills.models import SkillScope

__all__ = ["LOCAL_PLUGIN_METHODS", "ConnectorCatalogReader", "LocalPluginsController"]

logger = logging.getLogger(__name__)

LOCAL_PLUGIN_METHODS = frozenset({"plugins/local/list", "plugins/local/setEnabled"})

type SessionFiles = Callable[[Path | None], HarnessFilesManager]
type OrchestratorLoader = Callable[
    [HarnessFilesManager], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
]
# The connector catalog the process holds for the account a config names,
# never waiting on the service; None when it holds none.
type ConnectorCatalogReader = Callable[
    [ConfigOrchestrator[VibeConfigSchema]], Awaitable[ResolvedConnectorCatalog | None]
]


class LocalPluginsController:
    def __init__(
        self,
        session_files: SessionFiles,
        load_orchestrator: OrchestratorLoader,
        builtin_plugin_roots: Callable[[], Sequence[Path]],
        connector_catalog: ConnectorCatalogReader | None = None,
        credentials: PlatformCredentialFactory | None = None,
    ) -> None:
        """``session_files(None)`` is the user's own config, the one toggles write."""
        self._session_files = session_files
        self._load_orchestrator = load_orchestrator
        self._builtin_plugin_roots = builtin_plugin_roots
        self._connector_catalog = connector_catalog
        self._credentials = credentials

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> ProtocolModel:
        try:
            match method:
                case "plugins/local/list":
                    return await self._list(
                        validate_wire(PluginsLocalListParams, raw_params)
                    )
                case "plugins/local/setEnabled":
                    return await self._set_enabled(
                        validate_wire(PluginsLocalSetEnabledParams, raw_params)
                    )
                case _:
                    raise method_not_found(method)
        except ValidationError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc

    async def _list(self, params: PluginsLocalListParams) -> PluginsLocalListResponse:
        project_root = _project_root(params.project_root)
        user = await self._load_orchestrator(self._session_files(None))
        # Remote plugins are the user's, not a checkout's: synced once, on the
        # user's own listing. Not while a sync runs: the page polls the list
        # during a download, and each read must answer at once, not queue
        # another sync behind it.
        if project_root is None and not remote_plugins_syncing():
            await refresh_remote_plugins(
                user.config, self._credentials, trigger="plugins_page"
            )
        orchestrator, resolution = await self._read(
            project_root, params.trust_workspace
        )
        toggles = _toggles(orchestrator)
        remote = current_remote_plugins_state(user.config, self._credentials)
        catalog = await self._read_connector_catalog(orchestrator)
        listed = _listed(resolution, project_root)
        return PluginsLocalListResponse(
            remote_syncing=project_root is None and remote_plugins_syncing(),
            plugins=[
                *(
                    _summary(plugin, resolution, toggles, remote, catalog)
                    for plugin in listed
                ),
                *(
                    _unloaded_remote_summary(plugin, remote)
                    for plugin in _unloaded_remote(listed, remote, project_root)
                ),
            ],
            issues=[
                *(
                    LocalPluginIssue(file=str(issue.file), message=issue.message)
                    for issue in user_facing_issues(resolution.issues)
                ),
                # A name means one plugin on the page, so a library plugin
                # another of its name hides is reported rather than listed.
                *(
                    LocalPluginIssue(
                        # Not a path: the library plugin's ID, unique per row.
                        file=f"remote:{plugin.id}",
                        message=(
                            f"{plugin.title or plugin.name} is not loaded: "
                            f"another plugin in your library is named "
                            f"{plugin.name!r}"
                        ),
                    )
                    for plugin in (remote.shadowed if project_root is None else [])
                ),
                *(
                    _library_read_issue(read_error)
                    for read_error in [
                        remote_plugins_read_error(user.config, self._credentials)
                    ]
                    if read_error is not None and project_root is None
                ),
            ],
        )

    async def _set_enabled(
        self, params: PluginsLocalSetEnabledParams
    ) -> PluginsLocalSetEnabledResponse:
        project_root = _project_root(params.project_root)
        user = await self._load_orchestrator(self._session_files(None))
        remote = current_remote_plugins_state(user.config, self._credentials)
        remote_plugin = next((p for p in remote.plugins if p.name == params.name), None)
        if remote_plugin is not None and params.name in await self._builtin_names():
            # A built-in keeps its name: the switch is the built-in's.
            remote_plugin = None
        if remote_plugin is not None:
            if params.enabled:
                await self._refuse_remote_collision(remote_plugin)
            return await self._set_remote_enabled(remote_plugin, params.enabled)
        orchestrator, resolution = await self._read(
            project_root, params.trust_workspace
        )
        listed = _listed(resolution, project_root)
        plugin = next((p for p in listed if p.descriptor.name == params.name), None)
        if plugin is None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, f"No plugin named {params.name!r}"
            )
        toggles = _toggles(orchestrator)
        turn_off = (
            _rival_plugins(plugin, resolution)
            if params.enabled and params.turn_off_collisions
            else []
        )
        _refuse_toggle(
            plugin,
            params.enabled,
            toggles,
            resolution,
            turning_off={rival.descriptor.name for rival in turn_off},
        )
        mutation = await set_plugin_enabled(
            await self._load_orchestrator(self._session_files(None)),
            plugin.descriptor,
            params.enabled,
            turn_off=[rival.descriptor for rival in turn_off],
        )
        if mutation.failures:
            raise RequestFailure(
                ProtocolErrorCode.INTERNAL_ERROR,
                f"Failed to update configuration: {mutation.failures[0]}",
            ) from mutation.failures[0]
        # What the write did, without resolving again: an unlocked plugin is now
        # exactly what was asked, and the refusal above ruled out a collision.
        status: InstalledPluginStatus = "loaded" if params.enabled else "disabled"
        catalog = await self._read_connector_catalog(orchestrator)
        return PluginsLocalSetEnabledResponse(
            plugin=_summary(
                replace(plugin, status=status), resolution, toggles, remote, catalog
            ),
            turned_off=[rival.descriptor.name for rival in turn_off],
        )

    async def _builtin_names(self) -> set[str]:
        """The names Vibe's built-in plugins hold, which no library plugin takes."""
        _, resolution = await self._read(None, False)
        return {
            p.descriptor.name
            for p in resolution.installed
            if p.descriptor.scope is SkillScope.BUILTIN
        }

    async def _refuse_remote_collision(self, plugin: RemotePlugin) -> None:
        """Turning a library plugin on must not make a plugin that loads drop out.

        Its namespace is the one its name gives, unless its package (not on
        disk yet when it is off) names another: that rarer clash shows on its
        row once it is downloaded.
        """
        _, resolution = await self._read(None, False)
        namespace = typescript_identifier(plugin.name)
        rivals = sorted(
            other.descriptor.name
            for other in resolution.installed
            if other.descriptor.namespace == namespace
            and other.descriptor.name != plugin.name
            and other.status != "disabled"
        )
        if rivals:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS,
                f"{plugin.name!r} shares the namespace {namespace!r} with "
                f"{rivals[0]!r}, which is on: turn {rivals[0]!r} off first",
                LocalSkillRefusalData(
                    reason="namespace_collision", collides_with=rivals
                ).model_dump(mode="json"),
            )

    async def _set_remote_enabled(
        self, plugin: RemotePlugin, enabled: bool
    ) -> PluginsLocalSetEnabledResponse:
        """Written to le-chat, then synced, so the download follows the switch."""
        if not plugin.can_toggle:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS,
                f"{plugin.name!r} is turned on for everyone by a workspace admin",
                LocalSkillRefusalData(reason="locked").model_dump(mode="json"),
            )
        user = await self._load_orchestrator(self._session_files(None))
        try:
            state = await set_remote_plugin_enabled(
                user.config, plugin.id, enabled, self._credentials
            )
        except RemotePluginsError as exc:
            raise RequestFailure(
                ProtocolErrorCode.INTERNAL_ERROR,
                f"Could not turn {plugin.name!r} {'on' if enabled else 'off'}: "
                f"{exc.reason}",
            ) from exc
        updated = next((p for p in state.plugins if p.id == plugin.id), None)
        if updated is None or updated.enabled != enabled:
            # le-chat took the write, but the list it gave back since says
            # otherwise (or failed): report what was asked.
            updated = plugin.model_copy(update={"enabled": enabled})
        _, resolution = await self._read(None, False)
        loaded = next(
            (
                p
                for p in resolution.installed
                if p.descriptor.scope is SkillScope.REMOTE
                and p.descriptor.name == updated.name
            ),
            None,
        )
        if loaded is not None:
            toggles = PluginToggles(config=user.config, own_disabled=[])
            catalog = await self._read_connector_catalog(user)
            return PluginsLocalSetEnabledResponse(
                plugin=_summary(loaded, resolution, toggles, state, catalog)
            )
        return PluginsLocalSetEnabledResponse(
            plugin=_unloaded_remote_summary(updated, state)
        )

    async def _read_connector_catalog(
        self, orchestrator: ConfigOrchestrator[VibeConfigSchema]
    ) -> ResolvedConnectorCatalog | None:
        """The catalog held for the account, or None.

        Never waits for a fetch: reading it starts a background refresh when
        the catalog is old or missing, which the next list shows. A failure to read it leaves
        every connector ``unknown`` rather than failing the list.
        """
        if self._connector_catalog is None:
            return None
        try:
            return await self._connector_catalog(orchestrator)
        except Exception:
            logger.exception(
                "Failed to read the connector catalog for the plugins list"
            )
            return None

    async def _read(
        self, project_root: Path | None, trust_workspace: bool
    ) -> tuple[ConfigOrchestrator[VibeConfigSchema], ResolvedPluginSet]:
        """For a project, the config and plugins a session there runs with."""
        files = self._session_files(project_root)
        if project_root is not None and trust_workspace:
            files = files.trusting(project_root)
        orchestrator = await self._load_orchestrator(files)
        builtin_roots = self._builtin_plugin_roots()

        def resolve() -> ResolvedPluginSet:
            # Off the event loop: building the resolver checks every library
            # package's digest, which reads its whole tree.
            return PluginResolver.from_harness_files(
                files,
                builtin_roots=builtin_roots,
                data_root_base=VIBE_HOME.path / "plugin-data",
                config_orchestrator=orchestrator,
                credentials=self._credentials,
            ).resolve()

        return orchestrator, await asyncio.to_thread(resolve)


def _rival_plugins(
    plugin: InstalledPlugin, resolution: ResolvedPluginSet
) -> list[InstalledPlugin]:
    """The plugins on under its namespace that config can turn off.

    Vibe's own plugin stays on whatever config says, so it stays a rival and
    the toggle is refused. Another locked plugin is off, so it is no rival. A
    library plugin is turned off in le-chat, not in config, so it stays a
    rival too: the user turns it off from its own row.
    """
    rivals = set(_namespace_rivals(plugin, resolution))
    return [
        p
        for p in resolution.installed
        if p.descriptor.name in rivals
        and not is_required_plugin(p.descriptor)
        and p.descriptor.scope is not SkillScope.REMOTE
    ]


def _refuse_toggle(
    plugin: InstalledPlugin,
    enabled: bool,
    toggles: PluginToggles,
    resolution: ResolvedPluginSet,
    *,
    turning_off: AbstractSet[str] = frozenset(),
) -> None:
    name = plugin.descriptor.name
    if toggles.locked(plugin.descriptor):
        reason = (
            "is part of Vibe and cannot be turned off"
            if is_required_plugin(plugin.descriptor)
            else "is fixed by configuration and cannot be toggled here"
        )
        raise RequestFailure(
            ProtocolErrorCode.INVALID_PARAMS,
            f"{name!r} {reason}",
            LocalSkillRefusalData(reason="locked").model_dump(mode="json"),
        )
    rivals = [
        rival
        for rival in _namespace_rivals(plugin, resolution)
        if rival not in turning_off
    ]
    if enabled and rivals:
        namespace = plugin.descriptor.namespace
        rival = rivals[0]
        raise RequestFailure(
            ProtocolErrorCode.INVALID_PARAMS,
            f"{name!r} shares the namespace {namespace!r} with {rival!r}, which is "
            f"on: turn {rival!r} off first",
            LocalSkillRefusalData(
                reason="namespace_collision", collides_with=rivals
            ).model_dump(mode="json"),
        )


def _toggles(orchestrator: ConfigOrchestrator[VibeConfigSchema]) -> PluginToggles:
    return PluginToggles(
        config=orchestrator.config,
        own_disabled=list(writable_list_field(orchestrator, "disabled_plugins") or []),
    )


def _library_read_issue(error: str) -> LocalPluginIssue:
    """The library could not be read: say so, not an empty library."""
    return LocalPluginIssue(
        # Not a path: one row for the library as a whole.
        file="remote:library",
        message=(
            f"Your plugin library could not be read ({error}). The plugins "
            "synced before, if any, still load; the list refreshes on its own."
        ),
    )


def _listed(
    resolution: ResolvedPluginSet, project_root: Path | None
) -> list[InstalledPlugin]:
    """Without a project, the user's own and built-in plugins only."""
    return sorted(
        (
            plugin
            for plugin in resolution.installed
            if project_root is not None
            or plugin.descriptor.scope is not SkillScope.PROJECT
        ),
        key=lambda plugin: plugin.descriptor.name,
    )


def _namespace_rivals(
    plugin: InstalledPlugin, resolution: ResolvedPluginSet
) -> list[str]:
    """The other plugins on under the same namespace: all would drop out of sessions."""
    return sorted(
        other.descriptor.name
        for other in resolution.installed
        if other.descriptor.namespace == plugin.descriptor.namespace
        and other.descriptor.name != plugin.descriptor.name
        and other.status != "disabled"
    )


def _summary(
    plugin: InstalledPlugin,
    resolution: ResolvedPluginSet,
    toggles: PluginToggles,
    remote: RemotePluginsState,
    catalog: ResolvedConnectorCatalog | None,
) -> LocalPluginSummary:
    descriptor = plugin.descriptor
    if descriptor.scope is SkillScope.REMOTE:
        listed = remote_plugin_for_root(descriptor.root, remote)
        if listed is not None:
            return _remote_summary(plugin, resolution, listed, remote, catalog)
    return LocalPluginSummary(
        name=descriptor.name,
        display_name=descriptor.display_name,
        description=descriptor.description,
        version=descriptor.version,
        author=descriptor.author,
        scope=cast(PluginScope, descriptor.scope.value),
        source_format=descriptor.source_format.value,
        root=str(descriptor.root),
        components=_components(plugin),
        icon=_icon(descriptor.root),
        unsupported_parts=unsupported_parts(descriptor),
        starter_prompt=_starter_prompt(descriptor),
        enabled=plugin.status != "disabled",
        locked=toggles.locked(descriptor),
        required=is_required_plugin(descriptor),
        collides_with=_namespace_rivals(plugin, resolution)
        if plugin.status == "collides"
        else [],
        connectors=_connectors(plugin, catalog),
    )


def _starter_prompt(descriptor: PluginDescriptor) -> LocalPluginStarterPrompt | None:
    declared = descriptor.starter_prompt
    if declared is None:
        return None
    return LocalPluginStarterPrompt(
        prompt=dict(declared.prompt) if declared.prompt is not None else None,
        submit=declared.submit,
    )


def _remote_summary(
    plugin: InstalledPlugin,
    resolution: ResolvedPluginSet,
    listed: RemotePlugin,
    remote: RemotePluginsState,
    catalog: ResolvedConnectorCatalog | None,
) -> LocalPluginSummary:
    """A downloaded remote plugin: on/off and its lock come from le-chat."""
    descriptor = plugin.descriptor
    return LocalPluginSummary(
        name=descriptor.name,
        display_name=descriptor.display_name or listed.title,
        description=descriptor.description,
        version=descriptor.version,
        author=descriptor.author,
        scope="remote",
        source_format=descriptor.source_format.value,
        root=str(descriptor.root),
        components=_components(plugin),
        icon=_icon(descriptor.root),
        unsupported_parts=unsupported_parts(descriptor),
        starter_prompt=_starter_prompt(descriptor),
        enabled=listed.enabled,
        locked=not listed.can_toggle,
        required=False,
        collides_with=_namespace_rivals(plugin, resolution)
        if plugin.status == "collides"
        else [],
        remote=_remote_ref(listed, remote),
        connectors=_connectors(plugin, catalog),
    )


def _unloaded_remote(
    listed: Sequence[InstalledPlugin],
    remote: RemotePluginsState,
    project_root: Path | None,
) -> list[RemotePlugin]:
    """Remote plugins with no download to resolve: those that are off, or failed."""
    if project_root is not None:
        return []
    # A library plugin named like one listed here that is not its own (a
    # built-in keeps its name) is not shown: the name is the other's row.
    taken = {p.descriptor.name for p in listed}
    return sorted(
        (p for p in remote.plugins if p.name not in taken), key=lambda p: p.name
    )


def _unloaded_remote_summary(
    plugin: RemotePlugin, remote: RemotePluginsState
) -> LocalPluginSummary:
    return LocalPluginSummary(
        name=plugin.name,
        display_name=plugin.title,
        description=plugin.description or "",
        version=str(plugin.version),
        author=None,
        scope="remote",
        source_format="agent_plugins_1_0",
        root="",
        components=[],
        icon=plugin.icon,
        enabled=plugin.enabled,
        locked=not plugin.can_toggle,
        required=False,
        remote=_remote_ref(plugin, remote),
    )


def _remote_ref(
    plugin: RemotePlugin, remote: RemotePluginsState
) -> RemoteRegistryPluginRef:
    return RemoteRegistryPluginRef(
        plugin_id=plugin.id,
        version=plugin.version,
        title=plugin.title,
        owned_by_user=plugin.owned_by_user,
        sharing_scope=plugin.sharing_scope,
        error=remote.errors.get(plugin.id),
        pending=remote_plugin_pending(plugin.id),
    )


def _connectors(
    plugin: InstalledPlugin, catalog: ResolvedConnectorCatalog | None
) -> list[LocalPluginConnector]:
    """Matched by alias, as a session matches them (``RegistryConnectorCatalog``)."""
    by_alias = (
        {connector.alias: connector for connector in catalog.connectors}
        if catalog is not None
        else {}
    )
    return [
        LocalPluginConnector(
            id=definition.source_id,
            display_name=found.display_name if found is not None else None,
            connector_id=found.raw_id if found is not None else None,
            status=("unknown" if catalog is None else _connector_status(found)),
            tools=list(definition.tools),
        )
        for definition in plugin.connectors
        for found in (by_alias.get(definition.source_id),)
    ]


def _connector_status(
    connector: ResolvedConnector | None,
) -> LocalPluginConnectorStatus:
    if connector is None:
        return "unavailable"
    match connector_readiness(connector):
        case "ready":
            return "connected"
        case "needs_auth" | "needs_setup" | "needs_connect":
            return "not_connected"
        case "unavailable":
            return "unavailable"


def _components(plugin: InstalledPlugin) -> list[LocalPluginComponent]:
    def component(
        kind: PluginComponentKind, label: str, description: str | None
    ) -> LocalPluginComponent:
        return LocalPluginComponent(
            kind=kind, name=label, description=(description or "").strip() or None
        )

    return [
        *(
            component("skill", name, skill.description)
            for name, skill in plugin.skills.items()
        ),
        *(
            component("knowledge", item.display_name or item.name, item.description)
            for item in plugin.knowledge
        ),
        *(
            component(
                "subagent" if agent.profile.agent_type.value == "subagent" else "agent",
                agent.name,
                agent.profile.description,
            )
            for agent in plugin.agents
        ),
        *(
            component("library", library.alias, library.language.capitalize())
            for library in plugin.libraries
        ),
        *(
            component(
                "hook", hook.declared_name or hook.config.name, hook.config.description
            )
            for hook in plugin.runtime_hooks
        ),
        *(
            component("mcp_server", server.source_id, _server_address(server.server))
            for server in plugin.mcp_servers
        ),
        *(
            component("connector", connector.source_id, ", ".join(connector.tools))
            for connector in plugin.connectors
        ),
        *(
            component("view", name, description)
            for name, description in plugin_views(plugin.descriptor.root)
        ),
    ]


_ICON_PATH = Path("ai.mistral.vibe") / "icon.json"
# As Le Chat caps it (`PLUGIN_ICON_MAX_BYTES`): a drawn icon is under 1 KiB.
_ICON_MAX_BYTES = 4 * 1024


def _icon(root: Path) -> JsonValue:
    """The plugin's ``icon.json``, or None. Only drawn: the page validates it."""
    try:
        path = root / _ICON_PATH
        if path.stat().st_size > _ICON_MAX_BYTES:
            return None
        return cast(JsonValue, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _server_address(server: MCPServer) -> str:
    """The program a local server runs, or the address of a remote one."""
    if not isinstance(server, MCPStdio):
        return server.url
    command = server.command if isinstance(server.command, str) else server.command[0]
    return Path(command).name


def _project_root(value: str | None) -> Path | None:
    if value is None:
        return None
    root = Path(value).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise RequestFailure(
            ProtocolErrorCode.INVALID_PARAMS,
            f"Not a project folder: {value!r}",
            LocalSkillRefusalData(reason="not_a_project_folder").model_dump(
                mode="json"
            ),
        )
    return root.resolve()
