"""The Host side of plugin resolution for a Unified Harness session.

Resolution runs once per session context: the roots are discovered, every
plugin is parsed and materialized, and the result is projected into the
portable snapshot the Session Runtime reads. Only the Unified backend reaches
this module — the legacy backend never resolves plugins.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
import logging
from pathlib import Path
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
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.plugins import (
    MaterializedPluginSet,
    PluginAgentDefinition,
    PluginConfigIssue,
    PluginKnowledgeDefinition,
    PluginMaterializer,
    PluginPathRef,
    PluginResolver,
    ResolvedPluginSnapshot,
    build_snapshot,
    plugin_skill_runtime_path,
    resolve_plugin_path,
    snapshot_digest,
)
from vibe.core.skills.models import SkillInfo

if TYPE_CHECKING:
    from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustAgentTypeDefinition,
        RustKnowledgeFolderDefinition,
        RustPluginContextDefinition,
        RustSkillDefinition,
    )
    from mistralai_rust_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
        PluginLockV1,
    )

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SessionPlugins:
    """Everything one session needs to know about its plugins."""

    materialized: MaterializedPluginSet
    snapshot: ResolvedPluginSnapshot
    # The workdir discovery was rooted at, captured here rather than re-derived
    # from the process working directory when the catalogue is read: a client
    # asking what the session is running must not be answered from wherever the
    # reader happens to stand.
    workdir: Path

    @property
    def issues(self) -> tuple[PluginConfigIssue, ...]:
        return self.materialized.issues


async def resolve_session_plugins(
    harness_files: HarnessFilesManager,
    *,
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema] | None = None,
) -> SessionPlugins:
    """Resolve, materialize, and project the plugins visible to a session.

    Resolution never raises on a bad plugin: a plugin that fails to parse is
    dropped and reported through ``issues``, so one broken plugin cannot stop a
    session from starting.
    """
    resolution = PluginResolver.from_harness_files(
        harness_files, config_orchestrator=config_orchestrator
    ).resolve()
    materialized = await PluginMaterializer().materialize(resolution)
    return SessionPlugins(
        materialized=materialized,
        snapshot=build_snapshot(materialized),
        # The same fallback the manager applies when it walks for project roots,
        # so this is the directory discovery actually started from.
        workdir=harness_files.cwd or Path.cwd(),
    )


def plugin_lock(plugins: SessionPlugins) -> PluginLockV1:
    """Pin the plugin environment this session is created against.

    Each entry pins bytes: ``content_sha256`` is the tree digest of the plugin
    root. ``environment_sha256`` pins the environment as a whole — the entries
    plus the snapshot digest, which is what the Session Runtime is actually
    handed. Bytes alone are not enough: the snapshot folds in hook and MCP
    wiring that comes from config rather than from the plugin tree, so a plugin
    disabled in config moves the environment without moving a single byte.

    This detects; it does not restore. Nothing archives the plugin tree, so a
    resumed session reads whatever is on disk now — which is exactly why the
    comparison has to happen. ``validate_plugin_lock`` refuses the resume on a
    mismatch rather than continuing a history that references components the
    environment no longer has.
    """
    from mistralai_rust_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
        PluginLockEntryV1,
        PluginLockV1,
        sha256_json,
    )

    # The lock requires names sorted and unique. The resolver already drops name
    # collisions and sorts by name, but the lock's own ordering is the contract
    # being satisfied here, so sort against it rather than relying on that.
    entries = [
        PluginLockEntryV1(
            name=plugin.name,
            version=plugin.version or "",
            content_sha256=plugin.content_digest,
        )
        for plugin in sorted(
            plugins.materialized.resolution.plugins, key=lambda p: p.name
        )
    ]
    return PluginLockV1(
        environment_sha256=sha256_json({
            "plugins": [entry.model_dump(mode="json") for entry in entries],
            "snapshot": snapshot_digest(plugins.snapshot),
        }),
        plugins=entries,
    )


def plugin_issues(plugins: SessionPlugins) -> list[ConfigIssue]:
    """Project the resolution diagnostics onto the session's runtime snapshot.

    Dropping a broken plugin instead of failing the session is only a defensible
    policy if the operator is told which one went. ``runtime/read`` already
    carries the config issues the legacy backend reports, so a dropped plugin
    joins them there rather than opening a surface of its own.

    Resolution emits in pipeline order, which is not a contract, so the list is
    sorted: two Hosts that resolved the same tree report the same diagnostics in
    the same order. ``severity``, ``code`` and ``component`` stay Vibe-internal
    — the wire shape is file and message, and widening it is not this change.

    Nothing here needs redacting. A diagnostic never quotes a credential: the
    parse that would have seen one rejects the value with it left out.
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

    The snapshot's collections are keyed by kind; the public shape is one flat
    component list, so they fold into it plugin by plugin in snapshot order, and
    within a plugin in a fixed kind order. Two Hosts that resolved the same tree
    therefore render the same list, which is the whole point of the snapshot's
    canonical ordering being carried this far.

    Three kinds the contract declares are not emitted yet. ``mcp_server``,
    ``connector`` and ``tool`` are sourced from the snapshot's tool routes,
    connectors and tool groups, and those are still empty upstream — plugin MCP
    servers and connectors are not connected. Emitting them from anything else
    would report a catalogue the session does not have.

    ``config`` stays empty on every component. The contract has the field, but
    the only per-component configuration Vibe holds is a hook's, and a hook
    config is an operator's command line; the snapshot is Vibe-internal and this
    projection is not, so it is not widened here without a reason to.
    """
    snapshot = plugins.snapshot
    resolution = plugins.materialized.resolution
    roots = {plugin.name: plugin.root for plugin in resolution.plugins}
    pins = {plugin.name: plugin.content_digest for plugin in resolution.plugins}
    agent_kinds: dict[str, PluginComponentKind] = {
        definition.name: _agent_kind(definition) for definition in resolution.agents
    }
    return PluginInfo(
        workdir=str(plugins.workdir),
        components=[
            component
            for entry in snapshot.plugins
            for component in _plugin_components(
                snapshot, entry.name, roots, agent_kinds
            )
        ],
        # A debugging aid: the two digests answer "which plugin is this session
        # actually running". It is not a serialized snapshot: the snapshot
        # schema is Vibe-versioned and must not become an implicit public one by
        # being echoed through a fixed procedure.
        raw={
            "version": snapshot.version,
            "plugins": {
                entry.name: {
                    "manifestDigest": entry.manifest_digest,
                    "contentSha256": pins[entry.name],
                }
                for entry in snapshot.plugins
            },
        },
    )


def _plugin_components(
    snapshot: ResolvedPluginSnapshot,
    plugin: str,
    roots: Mapping[str, Path],
    agent_kinds: Mapping[str, PluginComponentKind],
) -> Iterator[PluginComponent]:
    """Fold one plugin's snapshot collections into the flat component list."""

    def owned[T: object](collection: Iterable[T]) -> Iterator[T]:
        return (
            item for item in collection if getattr(item, "plugin_name", None) == plugin
        )

    def component(
        kind: PluginComponentKind, name: str, ref: PluginPathRef | None
    ) -> PluginComponent:
        return PluginComponent(
            kind=kind, name=name, source_path=_source_path(ref, roots)
        )

    for skill in owned(snapshot.skills):
        yield component("skill", skill.name, skill.path)
    for folder in owned(snapshot.knowledge):
        yield component("knowledge", folder.name, folder.path)
    for agent in owned(snapshot.agents):
        yield component(agent_kinds.get(agent.name, "agent"), agent.name, agent.path)
    for library in owned(snapshot.libraries):
        yield component("library", library.alias, library.source_path)
    for hook in owned(snapshot.hooks):
        yield component("hook", hook.declared_name, hook.config_file)


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
    it reads SKILL.md, knowledge folders and agent files off disk itself. The
    snapshot stays the portable record; this is the local wiring.

    Two capability slots are deliberately left empty. Tool groups are still
    unpopulated upstream — ``MaterializedPluginSet`` produces none until plugin
    MCP servers and connectors are connected. Hook bindings are withheld because
    a bound hook makes Core emit a hook call action, and the Runtime has no
    adapter to execute one; binding them would fail turns rather than run hooks.
    """
    from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
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
    """Group plugin skills by owning plugin, keyed by the ``namespace:name``
    alias.

    Core accepts that alias verbatim, so the name the model sees is the name the
    resolver already assigned. The path is the runtime path, which for a foreign
    format is the SKILL.md synthesized during resolution rather than the
    plugin's own file.
    """
    from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
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
    reads what materialization produced and never the plugin tree itself. That
    copy is read-only: a plugin publishes knowledge, it does not host a
    scratchpad.
    """
    from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
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
    from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustAgentTypeDefinition,
    )

    grouped: dict[str, list[RustAgentTypeDefinition]] = defaultdict(list)
    for definition in definitions:
        agent = _accept(
            RustAgentTypeDefinition,
            definition.plugin_name,
            name=definition.name,
            description=definition.profile.description,
            path=str(definition.source_file),
        )
        if agent is not None:
            grouped[definition.plugin_name].append(agent)
    return grouped


def _accept[T](model: type[T], plugin: str, **fields: object) -> T | None:
    """Build a Core definition, dropping it if Core will not have it.

    Core validates harder than the resolver does — a blank skill description or
    a name that is not a valid alias is rejected there and accepted here. The
    resolver's contract is that one bad component never stops a session, so the
    component is dropped rather than allowed to fail config construction, which
    would take the whole session down.
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
