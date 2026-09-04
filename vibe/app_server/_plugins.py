"""The Host side of plugin resolution for a Unified Harness session.

Resolution runs once per session context: the roots are discovered, every
plugin is parsed and materialized, and the result is projected into the
portable snapshot the Session Runtime reads. Only the Unified backend reaches
this module — the legacy backend never resolves plugins.

This is also the single file in Vibe that implements the Session Runtime's
plugin port. `UnifiedPluginProvider` is the whole of the seam: everything
plugin-shaped stays on this side of it, and the Runtime below knows only names,
paths, and digests.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
import shutil
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import ValidationError

from vibe.agents import AgentType
from vibe.app_server.models import (
    ConfigIssue,
    PluginComponent,
    PluginComponentKind,
    PluginInfo,
)
from vibe.core.config import VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.models import MCPHttp, MCPStdio, MCPStreamableHttp
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.plugins import (
    DetectedPluginFormat,
    MaterializedPluginSet,
    PluginAgentDefinition,
    PluginConfigIssue,
    PluginKnowledgeDefinition,
    PluginMaterializer,
    PluginMCPServerDefinition,
    PluginPathRef,
    PluginResolver,
    PluginRouteKey,
    PluginRouteStatus,
    ResolvedPluginSet,
    ResolvedPluginSnapshot,
    build_snapshot,
    plugin_runtime_state_names,
    plugin_skill_runtime_path,
    reconcile_plugin_routes,
    redact_argv,
    redact_names,
    redact_url,
    resolve_plugin_path,
    retain_pinned_tools,
    snapshot_bytes,
    validate_resolved_plugin_snapshot,
)
from vibe.core.skills.models import SkillInfo
from vibe.core.tools.models import ToolPermission

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustAgentTypeDefinition,
        RustKnowledgeFolderDefinition,
        RustPluginContextDefinition,
        RustRuntimeBuiltinToolName,
        RustSkillDefinition,
    )
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        PluginInfo as HarnessPluginInfo,
        ResolvedPluginDefinition,
    )
    from mistralai_vibe_local_harness.vibe.plugins import (  # pyright: ignore[reportMissingImports]
        DeclaredAgentTypeProfile,
        PinnedPlugins,
        RestoredPlugins,
        SessionPluginProjection,
    )
    from pydantic import JsonValue

    from vibe.app_server._plugin_mcp import PluginMCPCatalog
    from vibe.core.config.models import MCPServer
    from vibe.core.tools.connectors.connector_registry import ConnectorRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentToolCatalogue:
    # Handed in rather than derived here: the catalogue is a function of the
    # session's live config, and this module resolves plugins, not tools. One
    # snapshot serves a whole bind, so its profiles cannot disagree about which
    # tools exist.
    available: frozenset[str]
    permission_of: Callable[[str], ToolPermission]


@dataclass(frozen=True, slots=True)
class SessionPlugins:
    """Everything one session needs to know about its plugins."""

    materialized: MaterializedPluginSet

    snapshot: ResolvedPluginSnapshot
    """The catalogue the session runs: at restore the pinned one, not this resolve's.

    A pinned tool the reconnected source dropped stays in it, marked; one the
    source now advertises that the pin does not carry is not added.
    """

    workdir: Path
    """Where discovery was rooted, so a reader is never answered from its own cwd."""

    routes: Mapping[PluginRouteKey, PluginRouteStatus] = MappingProxyType({})
    """Per-route status at restore. Empty at create, where every route is live."""

    @property
    def issues(self) -> tuple[PluginConfigIssue, ...]:
        return self.materialized.issues

    def route_status(self, group: str, function: str) -> PluginRouteStatus:
        """What a call into one route would do. ``live`` unless proven otherwise."""
        return self.routes.get((group, function), "live")


async def resolve_session_plugins(
    harness_files: HarnessFilesManager,
    *,
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema] | None = None,
    plugin_mcp: PluginMCPCatalog | None = None,
    connector_registry: ConnectorRegistry | None = None,
) -> SessionPlugins:
    """Resolve, materialize, and project the plugins visible to a session.

    Never raises on a bad plugin: it is dropped and reported through ``issues``,
    so one broken plugin cannot stop a session from starting.

    Without the MCP catalog and the connector registry no tools materialize,
    which is supported. The exception is a plugin declaring a managed connector
    while ``connector_registry`` is ``None``, which reports itself: an
    account-scoped source vanishing silently is a session the operator
    diagnoses by hand.
    """
    resolution = await asyncio.to_thread(
        _resolve_installed, harness_files, config_orchestrator
    )
    materialized = await _materialize(resolution, plugin_mcp, connector_registry)
    return SessionPlugins(
        materialized=materialized,
        snapshot=build_snapshot(materialized),
        workdir=harness_files.cwd or Path.cwd(),
    )


def _resolve_installed(
    harness_files: HarnessFilesManager,
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema] | None,
) -> ResolvedPluginSet:
    """Discover and resolve the installed plugin tree, off the event loop.

    Both halves belong in the thread. Discovery walks the project roots looking
    for plugin directories, and the resolve behind it reads and digests every
    file under each one it found — a plugin that vendors its dependencies is
    tens of thousands of syscalls, and the session opening behind this is what
    waits. ``bind`` offloads the same work for the same reason.
    """
    return PluginResolver.from_harness_files(
        harness_files, config_orchestrator=config_orchestrator
    ).resolve()


async def _materialize(
    resolution: ResolvedPluginSet,
    plugin_mcp: PluginMCPCatalog | None,
    connector_registry: ConnectorRegistry | None,
) -> MaterializedPluginSet:
    from vibe.core.plugins import RegistryConnectorCatalog

    # Before materialization, which is where the declared servers are connected.
    if plugin_mcp is not None:
        await plugin_mcp.bind(resolution.mcp_servers)
    materializer = PluginMaterializer(
        mcp_discovery=None if plugin_mcp is None else plugin_mcp.discovery(),
        connector_catalog=(
            None
            if connector_registry is None
            else RegistryConnectorCatalog(connector_registry)
        ),
    )
    return await materializer.materialize(resolution)


def requested_plugin_definitions(
    plugins: SessionPlugins,
) -> list[ResolvedPluginDefinition]:
    """Project a resolve onto the plugin set a session asks to be pinned.

    The request half of the seam, travelling as ``AgentConfig.plugins``. It
    names packages and the bytes they must have, and nothing about what is
    inside them: anything more would be a plugin concept the Session Runtime is
    not allowed to hold.

    Only plugins that survived resolution are named. Pinning a dropped one would
    record a package the session never had.
    """
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        ResolvedPluginDefinition,
    )

    return [
        ResolvedPluginDefinition(
            name=plugin.name,
            namespace=plugin.namespace,
            version=plugin.version,
            source_format=plugin.source_format.value,
            manifest_digest=plugin.manifest_digest,
            content_digest=plugin.content_digest,
        )
        for plugin in sorted(
            plugins.materialized.resolution.plugins, key=lambda item: item.name
        )
    ]


class PluginReloadUnavailableError(Exception):
    """This Runtime has no way to go looking for plugins a second time."""


class UnifiedPluginProvider:
    """Vibe's implementation of the Session Runtime's plugin port.

    Four methods, and only ``pin`` reads an installed directory. Everything
    after it is rooted at the read-only checkouts the Runtime rebuilt, which is
    what lets a session outlive the plugin being upgraded or uninstalled.

    One instance serves every session. Per-session state — the bound set and
    ``plugins/data/<session_id>/`` — is keyed by session id, because two
    sessions pinned to different content must not share a ``${PLUGIN_DATA}``.
    """

    def __init__(
        self,
        *,
        storage_root: Path,
        workdir: Path,
        installed_roots: Mapping[str, Path],
        config_orchestrator: ConfigOrchestrator[VibeConfigSchema] | None = None,
        plugin_mcp: PluginMCPCatalog | None = None,
        connector_registry: ConnectorRegistry | None = None,
        harness_files: HarnessFilesManager | None = None,
        agent_tools: Callable[[], AgentToolCatalogue] | None = None,
    ) -> None:
        self._plugins_root = Path(storage_root).expanduser().resolve() / "plugins"
        self._workdir = workdir
        self._installed_roots = dict(installed_roots)
        self._config_orchestrator = config_orchestrator
        self._plugin_mcp = plugin_mcp
        self._connector_registry = connector_registry
        self._harness_files = harness_files
        # Read once per bind. Absent, plugin agent types stay unexecutable: Core is
        # still told about them and the Host strips them for want of a binding, which
        # beats guessing at a ceiling with no catalogue to measure it against.
        self._agent_tools = agent_tools
        self._bound: dict[str, SessionPlugins] = {}
        self._binding: set[str] = set()

    def bound(self, session_id: str) -> SessionPlugins | None:
        """What this session is actually running, or ``None`` before its bind."""
        return self._bound.get(session_id)

    @property
    def installed_roots(self) -> Mapping[str, Path]:
        """Where each plugin was pinned from, as of the last scan.

        A name missing here was uninstalled since the pin, and the session
        holding its checkout is the last thing running it.
        """
        return dict(self._installed_roots)

    async def rescan(self) -> list[ResolvedPluginDefinition]:
        """Re-read the installed roots and report the pin they would produce now.

        The Host half of ``plugin/reload``. It re-runs the discovery that built
        this provider and adopts the roots it found, so a subsequent ``pin``
        hands over what was just seen rather than what the process started with.

        Nothing is bound and no session changes. The caller routes a difference
        through ``config/write``, which keeps re-pinning on the one path that
        knows how to fail without breaking a running session.

        Raises without the discovery inputs the Host resolved from: saying so
        beats rescanning an empty tree and reporting everything uninstalled.
        """
        if self._harness_files is None:
            raise PluginReloadUnavailableError(
                "this Runtime cannot rescan for plugins: no discovery roots were "
                "configured"
            )
        plugins = await resolve_session_plugins(
            self._harness_files,
            config_orchestrator=self._config_orchestrator,
            plugin_mcp=self._plugin_mcp,
            connector_registry=self._connector_registry,
        )
        self._installed_roots = {
            plugin.name: plugin.root
            for plugin in plugins.materialized.resolution.plugins
        }
        return requested_plugin_definitions(plugins)

    async def pin(
        self, requested: Sequence[ResolvedPluginDefinition], *, session_id: str
    ) -> PinnedPlugins:
        """Hand over the installed root of every plugin the request names.

        The only read of an installed directory in the design, and it reads
        nothing but the path. The roots come from the resolve that produced the
        request, so they disagree only if the tree moved in between — which the
        Runtime catches when the ingest digest misses.

        A name this provider does not hold is left out rather than guessed at,
        and the Runtime turns the gap into ``plugin_pin_mismatch``.

        Each root travels with the names the ingest must prune from it, taken
        off the request's own ``source_format`` so it is the set the resolve
        that produced the request digested with. Deriving it here from
        anything else would reintroduce the mismatch it exists to prevent.
        """
        from mistralai_vibe_local_harness.vibe.plugins import (  # pyright: ignore[reportMissingImports]
            PinnedPackage,
            PinnedPlugins,
        )

        return PinnedPlugins(
            packages={
                definition.name: PinnedPackage(
                    root=self._installed_roots[definition.name],
                    ignored_names=plugin_runtime_state_names(
                        DetectedPluginFormat(definition.source_format)
                    ),
                )
                for definition in requested
                if definition.name in self._installed_roots
            }
        )

    async def bind(
        self, plugins: RestoredPlugins, *, session_id: str
    ) -> tuple[bytes, SessionPluginProjection]:
        """Resolve, materialize, snapshot, and project one session's checkouts.

        Nothing installed is read. The checkouts are handed to the resolver as
        the plugin directories outright, so ``plugin.root`` and every
        ``${PLUGIN_ROOT}`` land inside the tree the Runtime rebuilt.

        The set is assembled in locals and published in one assignment, so a
        failed re-bind leaves the previous set bound and running and a rejected
        ``config/write`` is a no-op rather than a broken session.

        ``plugins.snapshot`` decides which catalogue is published. Bytes are a
        restore, and the pinned catalogue wins: the remote schemas behind it
        were never covered by a package digest, so the fresh derivation is
        measured against the pin rather than replacing it. ``None`` is a create,
        where the derivation is the pin — unless this session already had one,
        which makes it a re-pin that carries the tools the derivation lost.
        """
        self._binding.add(session_id)
        previous = self._bound.get(session_id)
        resolution = await asyncio.to_thread(
            self._resolve, session_id, plugins.checkouts
        )
        materialized = await _materialize(
            resolution, self._plugin_mcp, self._connector_registry
        )
        derived = build_snapshot(materialized)
        published = _pinned_snapshot(plugins.snapshot)
        if published is None:
            published = (
                derived
                if previous is None
                else retain_pinned_tools(derived, previous.snapshot)
            )
        bound = SessionPlugins(
            materialized=materialized,
            snapshot=published,
            workdir=self._workdir,
            routes=(
                MappingProxyType({})
                if published is derived
                else reconcile_plugin_routes(published, materialized)
            ),
        )
        self._bound[session_id] = bound
        self._binding.discard(session_id)
        # Derived, never published: the lock records this resolve, not the
        # ghosts the bound catalogue carries. A restore discards these anyway.
        return snapshot_bytes(derived), self._project(bound)

    def _project(self, bound: SessionPlugins) -> SessionPluginProjection:
        # Both halves out of the same resolve: an advertised agent type with no
        # profile is a name the model can call and the Runtime cannot spawn.
        from mistralai_vibe_local_harness.vibe.plugins import (  # pyright: ignore[reportMissingImports]
            SessionPluginProjection,
        )

        definitions = tuple(core_plugins(bound))
        agents = list(bound.materialized.resolution.agents)
        if not agents:
            return SessionPluginProjection(definitions=definitions)
        if self._agent_tools is None:
            logger.warning(
                "Not binding %d plugin agent type(s): no tool catalogue to "
                "resolve their ceilings against",
                len(agents),
            )
            return SessionPluginProjection(definitions=definitions)
        return SessionPluginProjection(
            definitions=definitions,
            agent_profiles=declared_agent_profiles(agents, self._agent_tools()),
        )

    async def info(self, *, session_id: str) -> HarnessPluginInfo:
        """Project the bound set for ``plugin/info``.

        The Runtime forwards this without reading a field, so what crosses the
        seam is the shared wire model rather than a Vibe type. A session that
        has not bound answers with an empty catalogue: a read is not the place
        to raise, and "nothing is bound" is an answer.
        """
        from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
            PluginInfo as HarnessPluginInfo,
        )

        bound = self._bound.get(session_id)
        if bound is None:
            return HarnessPluginInfo()
        return HarnessPluginInfo.model_validate(
            plugin_info(bound).model_dump(mode="json", by_alias=True)
        )

    async def release(self, *, session_id: str) -> None:
        """Drop a session's bound set and the files its bind staged.

        Never raises: the Runtime calls this in a ``finally``.

        After a failed ``bind`` nothing was published, so the previous set is
        still the one in ``_bound`` and this returns without touching it —
        otherwise a rejected re-pin would take a working session's plugins down.

        ``${PLUGIN_DATA}`` survives, being the session's own durable state. Only
        the staged runtime files go, and the next bind rebuilds them.
        """
        if session_id in self._binding:
            self._binding.discard(session_id)
            return
        self._bound.pop(session_id, None)
        try:
            await asyncio.to_thread(self._discard_runtime_files, session_id)
        except Exception:
            logger.warning(
                "Failed to drop the staged plugin runtime files of session %r",
                session_id,
                exc_info=True,
            )

    def _resolve(
        self, session_id: str, checkouts: Mapping[str, Path]
    ) -> ResolvedPluginSet:
        """Resolve the checkouts as the plugin set, with a per-session data root.

        Sorted by plugin name so the order the resolver sees does not depend on
        the order the Runtime happened to check the packages out.
        """
        resolution = PluginResolver(
            plugin_dirs=[checkouts[name] for name in sorted(checkouts)],
            data_root_base=self._data_root(session_id),
            config_orchestrator=self._config_orchestrator,
        ).resolve()
        for plugin in resolution.plugins:
            # ${PLUGIN_DATA} is handed out as writable, so it has to exist first.
            plugin.data_root.mkdir(parents=True, exist_ok=True)
        return resolution

    def _data_root(self, session_id: str) -> Path:
        return self._plugins_root / "data" / session_id

    def _discard_runtime_files(self, session_id: str) -> None:
        shutil.rmtree(self._data_root(session_id) / ".runtime", ignore_errors=True)


def _pinned_snapshot(blob: bytes | None) -> ResolvedPluginSnapshot | None:
    """The catalogue a restore recorded, or ``None`` when this is a create.

    Empty bytes mean "restored, and the lock pinned no plugins" — nothing to
    honour and nothing to compare, so it reads the same as a create.

    A blob that will not parse fails the restore. The bytes are digest-verified
    by the time they arrive, so the only way here is a schema this build no
    longer understands, and a session whose recorded catalogue cannot be read
    cannot have its history honestly replayed.
    """
    if not blob:
        return None

    from mistralai_vibe_local_harness.vibe.plugins import (  # pyright: ignore[reportMissingImports]
        PluginRestoreDiagnostic,
        PluginRestoreDiagnosticCode,
        PluginRestoreError,
    )

    try:
        snapshot = ResolvedPluginSnapshot.model_validate_json(blob)
        validate_resolved_plugin_snapshot(snapshot)
    except (ValidationError, ValueError) as error:
        raise PluginRestoreError([
            PluginRestoreDiagnostic(
                code=PluginRestoreDiagnosticCode.LOCK_INVALID,
                message=f"the pinned plugin snapshot cannot be read: {error}",
            )
        ]) from error
    return snapshot


def plugin_issues(plugins: SessionPlugins) -> list[ConfigIssue]:
    """Project the resolution diagnostics onto the session's runtime snapshot.

    Dropping a broken plugin is only defensible if the operator is told which
    one went, and ``runtime/read`` already carries config issues, so a dropped
    plugin joins them there rather than opening a surface of its own.

    Sorted, because resolution emits in pipeline order and that is not a
    contract. ``severity``, ``code`` and ``component`` stay Vibe-internal.

    Nothing is redacted here, because every diagnostic arrives redacted. A
    parse that would have seen a credential rejects the value with it left out,
    and a failure quoted from a client Vibe does not author is stripped where
    it is caught (`redact_failure`). This surface publishes the message
    verbatim, so a diagnostic that carries a secret has already leaked it.
    """
    return [
        ConfigIssue(file=str(issue.file), message=issue.message)
        for issue in sorted(
            plugins.issues,
            key=lambda item: (str(item.file), item.code or "", item.message),
        )
    ]


def plugin_info(plugins: SessionPlugins) -> PluginInfo:
    """Project the resolved plugins into the public ``plugin/info`` catalogue.

    The snapshot's collections are keyed by kind and the public shape is one
    flat list, so they fold into it plugin by plugin in snapshot order and
    within a plugin in a fixed kind order. Two Hosts that resolved the same tree
    render the same list, which is what the canonical ordering is carried for.

    ``mcp_server`` is the one kind not read off the snapshot, which carries the
    catalog those servers answered with and no server definitions. It comes off
    the materialized set, widened by the servers the pinned routes name: at
    restore a server that did not answer still owns tools the session runs. One
    that never answered and owns nothing is reported through ``plugin_issues``
    instead of listed as part of an environment nobody has.

    ``config`` is populated on that kind alone, through the redacting
    projection. The only other per-component configuration Vibe holds is a
    hook's, and a hook config is an operator's command line.
    """
    snapshot = plugins.snapshot
    resolution = plugins.materialized.resolution
    pins = {plugin.name: plugin.content_digest for plugin in resolution.plugins}
    sources = _component_sources(plugins)
    return PluginInfo(
        workdir=str(plugins.workdir),
        components=[
            component
            for entry in snapshot.plugins
            for component in _plugin_components(sources, entry.name)
        ],
        # A debugging aid, not a serialized snapshot: that schema is
        # Vibe-versioned and must not become public by being echoed here.
        raw={
            "version": snapshot.version,
            "plugins": {
                entry.name: {
                    "manifestDigest": entry.manifest_digest,
                    # Absent once a pinned plugin is uninstalled. The session
                    # runs off the checkout either way.
                    "contentSha256": pins.get(entry.name),
                }
                for entry in snapshot.plugins
            },
            # Only the routes that moved: a component absent here is live.
            "routes": {
                f"{group}.{function}": status
                for (group, function), status in sorted(plugins.routes.items())
                if status != "live"
            },
        },
    )


@dataclass(frozen=True, slots=True)
class PluginRouteFailure:
    """Why a call into a pinned tool route must not be executed."""

    code: str
    message: str

    retryable: bool = False
    """Never. Both causes are settled facts about this bind, so a retry repeats them."""


def plugin_route_failure(
    plugins: SessionPlugins, group: str, function: str
) -> PluginRouteFailure | None:
    """The failure a call into one route must produce, or ``None`` to proceed.

    The check ``schema_fingerprint`` exists for. A remote tool's schema is the
    one part of a plugin environment a package digest cannot pin, so the
    fingerprint recorded at pin time is compared against what the source
    answered with at bind.

    The conversation was built against the pinned schema — the model chose
    arguments for it, the journal records results shaped by it — so running
    those arguments against a schema that moved is not a degraded call but a
    call into a different tool wearing the same name.
    """
    status = plugins.route_status(group, function)
    if status == "live":
        return None
    name = f"{group}.{function}"
    if status == "stale":
        return PluginRouteFailure(
            code="plugin_catalog_changed",
            message=(
                f"The tool {name!r} is not the tool this session pinned: its source "
                "now advertises a different schema, or no longer advertises it at "
                "all. Start a new session to pick up the current catalogue."
            ),
        )
    return PluginRouteFailure(
        code="plugin_source_unavailable",
        message=(
            f"The tool {name!r} cannot be called because its source did not answer "
            "when this session was restored. Reload the plugins once the source is "
            "reachable again."
        ),
    )


_RELOAD_NOTICE_CODES = frozenset({
    "plugin.mcp.connection_failed",
    "plugin.connector.unavailable",
    "plugin.connector.runtime_unavailable",
})


def plugin_reload_notices(plugins: SessionPlugins) -> list[str]:
    """What a reload has to say when it worked and still did not fix everything.

    A reload that reaches here succeeded. What is left is the part a rescan
    cannot repair — a source still unreachable, and the tools the session keeps
    carrying because of it — which is a remark, not a failed command. There is
    no plugin event in the closed union, so a notice is the whole signal and a
    Client that cares re-reads ``plugin/info``.
    """
    notices = [
        issue.message
        for issue in sorted(plugins.issues, key=lambda item: item.message)
        if issue.code in _RELOAD_NOTICE_CODES
    ]
    unavailable = sorted(
        f"{group}.{function}"
        for (group, function), status in plugins.routes.items()
        if status == "unavailable"
    )
    if unavailable:
        notices.append(
            "These plugin tools are no longer offered by their source and calling "
            f"one will fail: {', '.join(unavailable)}."
        )
    return notices


def plugin_components_by_owner(
    plugins: SessionPlugins,
) -> Mapping[str, tuple[PluginComponent, ...]]:
    """The same catalogue ``plugin/info`` publishes, kept attributed to its owners.

    ``plugin/info`` folds these into one flat list, and three of the kinds
    carry a name no client can trace back to a plugin. A Vibe-owned reader in
    this process keeps the boundaries the projection drops.
    """
    sources = _component_sources(plugins)
    return {
        entry.name: tuple(_plugin_components(sources, entry.name))
        for entry in plugins.snapshot.plugins
    }


@dataclass(frozen=True, slots=True)
class _ComponentSources:
    """The inputs the flat component list is folded from, prepared once."""

    snapshot: ResolvedPluginSnapshot
    roots: Mapping[str, Path]
    agent_kinds: Mapping[str, PluginComponentKind]
    mcp_servers: tuple[PluginMCPServerDefinition, ...]


def _component_sources(plugins: SessionPlugins) -> _ComponentSources:
    materialized = plugins.materialized
    resolution = materialized.resolution
    listed = materialized.connected_mcp_sources | {
        (route.plugin_name, route.source_id)
        for route in plugins.snapshot.tool_routes
        if route.source_kind == "mcp"
    }
    return _ComponentSources(
        snapshot=plugins.snapshot,
        roots={plugin.name: plugin.root for plugin in resolution.plugins},
        agent_kinds={
            definition.name: _agent_kind(definition) for definition in resolution.agents
        },
        mcp_servers=tuple(
            sorted(
                (
                    definition
                    for definition in resolution.mcp_servers
                    if (definition.plugin_name, definition.source_id) in listed
                ),
                key=lambda item: (item.plugin_name, item.source_id),
            )
        ),
    )


def _plugin_components(
    sources: _ComponentSources, plugin: str
) -> Iterator[PluginComponent]:
    """Fold one plugin's snapshot collections into the flat component list."""
    snapshot = sources.snapshot

    def owned[T: object](collection: Iterable[T]) -> Iterator[T]:
        return (
            item for item in collection if getattr(item, "plugin_name", None) == plugin
        )

    def component(
        kind: PluginComponentKind, name: str, ref: PluginPathRef | None
    ) -> PluginComponent:
        return PluginComponent(
            kind=kind, name=name, source_path=_source_path(ref, sources.roots)
        )

    for skill in owned(snapshot.skills):
        yield component("skill", skill.name, skill.path)
    for folder in owned(snapshot.knowledge):
        yield component("knowledge", folder.name, folder.path)
    for agent in owned(snapshot.agents):
        yield component(
            sources.agent_kinds.get(agent.name, "agent"), agent.name, agent.path
        )
    for library in owned(snapshot.libraries):
        yield component("library", library.alias, library.source_path)
    for hook in owned(snapshot.hooks):
        yield component("hook", hook.declared_name, hook.config_file)
    for definition in owned(sources.mcp_servers):
        yield PluginComponent(
            kind="mcp_server",
            name=definition.source_id,
            source_path=str(definition.config_file),
            config=_mcp_server_config(definition.server),
        )
    for connector in owned(snapshot.connectors):
        yield component("connector", connector.source_id, None)
    for group in owned(snapshot.tool_groups):
        for tool in group.tools:
            yield component("tool", f"{group.name}.{tool.name}", None)


def _mcp_server_config(server: MCPServer) -> dict[str, JsonValue]:
    """Project one MCP server onto the redacted shape ``plugin/info`` publishes.

    Positional, per the contract's redaction rule: a fixed list of positions
    known to hold no credential, not a dump with the risky keys removed. A field
    added to the config model upstream stays out until someone adds it here.

    The argument vector is the one position that looked safe and is not, so it
    is published by name like ``env`` and the headers rather than verbatim.
    """
    if isinstance(server, MCPStdio):
        return {
            "transport": server.transport,
            "argv": list(redact_argv(server.argv())),
            "env": list(redact_names(server.env)),
            "cwd": server.cwd,
        }
    if isinstance(server, MCPHttp | MCPStreamableHttp):
        return {
            "transport": server.transport,
            "url": redact_url(server.url),
            "headers": list(redact_names(server.http_headers())),
        }
    return {}


def _agent_kind(definition: PluginAgentDefinition) -> PluginComponentKind:
    """Split agents by the type their document declares."""
    return (
        "subagent" if definition.profile.agent_type is AgentType.SUBAGENT else "agent"
    )


def _source_path(ref: PluginPathRef | None, roots: Mapping[str, Path]) -> str | None:
    """Join a portable reference against the checkout root, at read time.

    A component with no file on disk keeps no source path, and a reference to a
    plugin that is not installed is dropped rather than guessed at.
    """
    if ref is None or ref.plugin not in roots:
        return None
    return str(resolve_plugin_path(ref, dict(roots)))


def core_plugins(plugins: SessionPlugins) -> list[RustPluginContextDefinition]:
    """Project the resolved plugins into the Harness Core configuration.

    The lock pins what the plugin environment *is*; this is what Core can *use*.
    Core is handed real paths rather than the snapshot's portable refs, because
    it reads SKILL.md, knowledge folders and agent files off disk itself.

    Tool groups and hook bindings are withheld: Core calls what it advertises,
    and neither has an execution adapter yet, so advertising them would fail
    turns rather than run anything. The tool catalogue still reaches the
    snapshot and ``plugin/info``.
    """
    from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustHarnessCapabilitySet,
        RustPluginContextDefinition,
    )

    resolution = plugins.materialized.resolution
    owners = {plugin.namespace: plugin.name for plugin in resolution.plugins}
    skills = _core_skills(resolution.skills, owners)
    knowledge = _core_knowledge(plugins.materialized.knowledge)
    agents = _core_agents(resolution.agents)
    return [
        RustPluginContextDefinition(
            name=plugin.name,
            description=plugin.description,
            path=str(plugin.root),
            capabilities=RustHarnessCapabilitySet(
                skills=skills[plugin.name],
                knowledge_folders=knowledge[plugin.name],
                agent_types=agents[plugin.name],
            ),
        )
        for plugin in sorted(resolution.plugins, key=lambda item: item.name)
    ]


def _core_skills(
    skills: Mapping[str, SkillInfo], owners: Mapping[str, str]
) -> Mapping[str, list[RustSkillDefinition]]:
    """Group plugin skills by owning plugin, under their ``namespace:name`` alias.

    Core accepts that alias verbatim, so the name the model sees is the one the
    resolver assigned. The path is the runtime path, which for a foreign format
    is the SKILL.md synthesized during resolution.
    """
    from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustSkillDefinition,
    )

    grouped: dict[str, list[RustSkillDefinition]] = defaultdict(list)
    for alias, skill in skills.items():
        namespace, _, _ = alias.partition(":")
        owner = owners.get(namespace)
        path = plugin_skill_runtime_path(skill)
        if owner is None or path is None:
            continue
        definition = _accept(
            RustSkillDefinition,
            owner,
            name=alias,
            description=skill.description,
            path=str(path),
        )
        if definition is not None:
            grouped[owner].append(definition)
    return grouped


def _core_knowledge(
    definitions: Iterable[PluginKnowledgeDefinition],
) -> Mapping[str, list[RustKnowledgeFolderDefinition]]:
    """Group materialized knowledge folders by owning plugin.

    The runtime root is the staged copy under the plugin data root, so Core
    never reads the plugin tree itself. It is read-only: a plugin publishes
    knowledge, it does not host a scratchpad.
    """
    from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustKnowledgeFolderDefinition,
    )

    grouped: dict[str, list[RustKnowledgeFolderDefinition]] = defaultdict(list)
    for definition in definitions:
        folder = _accept(
            RustKnowledgeFolderDefinition,
            definition.plugin_name,
            name=definition.name,
            description=definition.description,
            path=str(definition.runtime_root),
            access="read_only",
        )
        if folder is not None:
            grouped[definition.plugin_name].append(folder)
    return grouped


def _core_agents(
    definitions: Iterable[PluginAgentDefinition],
) -> Mapping[str, list[RustAgentTypeDefinition]]:
    """Group plugin agent types by owning plugin."""
    from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustAgentTypeDefinition,
    )

    grouped: dict[str, list[RustAgentTypeDefinition]] = defaultdict(list)
    for definition in _agent_definitions(definitions):
        agent = _accept(
            RustAgentTypeDefinition,
            definition.plugin_name,
            name=definition.name,
            description=definition.profile.description,
            path=_agent_profile_path(definition),
        )
        if agent is not None:
            grouped[definition.plugin_name].append(agent)
    return grouped


def _agent_definitions(
    definitions: Iterable[PluginAgentDefinition],
) -> Iterator[PluginAgentDefinition]:
    # Core validates agent types for the whole configuration rather than per entry,
    # so one blank description invalidates every plugin's capabilities. ``_accept``
    # does not catch it: ``AgentProfile.from_toml`` defaults ``description`` to the
    # empty string and ``RustAgentTypeDefinition`` has no length floor.
    for definition in definitions:
        if not definition.name.strip() or not definition.profile.description.strip():
            logger.warning(
                "Dropped agent type %r from plugin %r: name and description are required",
                definition.name,
                definition.plugin_name,
            )
            continue
        yield definition


def _agent_profile_path(definition: PluginAgentDefinition) -> str:
    # One expression, shared by the advertised type and the profile behind it. If the
    # two drifted, the model would read one file and the child would spawn from another.
    return str(definition.source_file)


def declared_agent_profiles(
    definitions: Iterable[PluginAgentDefinition], tools: AgentToolCatalogue
) -> tuple[DeclaredAgentTypeProfile, ...]:
    # Everything the Runtime needs to spawn a child and nothing it would have to be
    # Vibe to read. ``active_model`` and ``safety`` are deliberately not carried: the
    # first is a widening, since the policy ceiling grants exactly one completion, and
    # the second gates nothing today.
    from mistralai_vibe_local_harness.vibe.plugins import (  # pyright: ignore[reportMissingImports]
        DeclaredAgentTypeProfile,
    )

    from vibe.app_server._runtime import rust_agent_tool_ceiling

    profiles: list[DeclaredAgentTypeProfile] = []
    for definition in _agent_definitions(definitions):
        overrides = definition.profile.overrides
        for key in ("active_model", "model"):
            if key in overrides:
                logger.warning(
                    "Agent type %r from plugin %r declares %r; it runs on the "
                    "session's model instead",
                    definition.name,
                    definition.plugin_name,
                    key,
                )
        ceiling = rust_agent_tool_ceiling(
            set(tools.available), tools.permission_of, overrides
        )
        path = _agent_profile_path(definition)
        profile = _accept(
            DeclaredAgentTypeProfile,
            definition.plugin_name,
            agent_type=definition.name,
            description=definition.profile.description,
            profile_path=path,
            instructions=definition.profile.instructions,
            tool_ceiling=ceiling,
            authority_digest=_agent_authority_digest(definition, path, ceiling),
        )
        if profile is not None:
            profiles.append(profile)
    return tuple(profiles)


def _agent_authority_digest(
    definition: PluginAgentDefinition,
    path: str,
    ceiling: Mapping[RustRuntimeBuiltinToolName, str],
) -> str:
    # Folded into the binding's template digest. A child spawned under an older
    # authority keeps the template it was admitted with, and only the digest tells
    # the two apart.
    payload = json.dumps(
        {
            "plugin": definition.plugin_name,
            "source": definition.source_name,
            "path": path,
            "description": definition.profile.description,
            "instructions": definition.profile.instructions,
            "ceiling": dict(sorted(ceiling.items())),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _accept[T](model: type[T], plugin: str, **fields: object) -> T | None:
    """Build a Core definition, dropping it if Core will not have it.

    Core validates harder than the resolver — a blank description or a name that
    is not a valid alias passes here and is rejected there. One bad component
    never stops a session, so it is dropped rather than allowed to fail config
    construction and take the session down.
    """
    try:
        return model(**fields)  # pyright: ignore[reportCallIssue]
    except ValidationError as error:
        logger.warning(
            "Dropped %s from plugin %r: %s",
            model.__name__,
            plugin,
            error,
            exc_info=True,
        )
        return None
